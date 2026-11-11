"""模拟盘引擎（issue #26，spec §8/§11/§12）。

与回测共用撮合（backtest.matching）与指标（backtest.metrics）模块：
- on_close(date)：收盘后跑决策（#22 入场信号 + #23 daily_review 出场/仓位/熔断），
  指令落 risk_pending_order（次日开盘执行）；
- on_open(date)：次日开盘按真实开盘价虚拟成交（涨跌停顺延），成交/持仓/净值落表，
  与回测一致口径（滑点、费用、T+1 卖出资金结算、整手）；
- run(start, end)：按交易日序贯跑完整链路；
- paper_report(db)：读回净值/指标/持仓，供 /api/paper 与前台展示。

状态（现金/持仓/净值高点）持久化于 portfolio_* 表，进程重启后可续跑。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import text

from app.backtest import matching
from app.backtest.engine import industry_of
from app.backtest.metrics import compute_metrics
from app.common.config import RiskConfig
from app.common.enums import SIGNAL_STRONG, normalize_signal_level
from app.data.db import Database
from app.data.price_query import PriceQuery
from app.common.portfolio_models import (
    PortfolioNav, PortfolioPosition, PortfolioTrade,
)
from app.strategy.pool_models import PoolBase
from app.strategy.risk import exits, sizing
from app.strategy.risk.circuit import CircuitBreaker
from app.strategy.risk.engine import RiskBase, daily_review, get_pending_orders
from app.strategy.signals import generate_entry_signals

# 初始资金单一来源：RiskConfig.initial_capital（默认 200000）
INITIAL_CAPITAL = RiskConfig().initial_capital


@dataclass
class PaperState:
    cash: float = INITIAL_CAPITAL          # 已结算可用现金
    unsettled: float = 0.0                 # 当日卖出所得（T+1）
    highest_nav: float = INITIAL_CAPITAL
    highest_closes: dict = field(default_factory=dict)  # code -> highest close
    last_nav_date: str = ""


def _trading_dates(db: Database, start: str, end: str) -> list[str]:
    with db.session() as s:
        rows = s.execute(text(
            "SELECT DISTINCT date FROM daily_bar WHERE date BETWEEN :s AND :e"
            " ORDER BY date"), {"s": start, "e": end}).all()
    return [str(r[0]) for r in rows]


class PaperTradingEngine:
    def __init__(self, db: Database, config: RiskConfig,
                 initial_capital: float | None = None):
        self.db = db
        self.config = config
        self.initial_capital = initial_capital or config.initial_capital
        self._pq = PriceQuery(db)
        PoolBase.metadata.create_all(db.engine)
        RiskBase.metadata.create_all(db.engine)
        from app.common.portfolio_models import PortfolioBase

        PortfolioBase.metadata.create_all(db.engine)
        self.state = self._load_state()

    # -- 状态持久化 ---------------------------------------------------------

    def _load_state(self) -> PaperState:
        st = PaperState(cash=self.initial_capital, highest_nav=self.initial_capital)
        with self.db.session() as s:
            navs = s.query(PortfolioNav).order_by(PortfolioNav.date).all()
        if navs:
            # 从落表重建：cash = 最新净值 × 初始资金 - 持仓市值，由 on_open 前重算
            st.last_nav_date = navs[-1].date
            st.highest_nav = max(n.nav for n in navs) * self.initial_capital
            st.cash = self._rebuild_cash(navs[-1].nav)
        return st

    def _rebuild_cash(self, last_nav: float) -> float:
        """简化重建：现金 = 总权益 − 持仓按最新收盘市值（成交历史精确推导见 _positions_value）。"""
        pos_value = self._positions_value(None)
        return last_nav * self.initial_capital - pos_value

    def _positions(self) -> list[PortfolioPosition]:
        with self.db.session() as s:
            return s.query(PortfolioPosition).all()

    def _positions_value(self, date: str | None) -> float:
        total = 0.0
        for p in self._positions():
            total += p.shares * (self._close(p.code, date) or p.entry_price)
        return total

    def _close(self, code: str, dt: str | None) -> float | None:
        return self._pq.get_close(code, dt)

    def _open(self, code: str, dt: str) -> tuple[float | None, float | None]:
        return self._pq.get_open_with_prev_close(code, dt)

    def _equity(self, dt: str) -> float:
        return self.state.cash + self.state.unsettled + self._positions_value(dt)

    # -- 开盘：虚拟成交（与回测 matching 同口径） -----------------------------

    def on_open(self, date: str) -> list[PortfolioTrade]:
        """次日开盘按真实开盘价执行待成交指令；涨跌停顺延（留 pending）。"""
        st = self.state
        st.cash += st.unsettled
        st.unsettled = 0.0

        filled: list[PortfolioTrade] = []
        orders = get_pending_orders(self.db, date)
        sells = [o for o in orders if o.action == "SELL"]
        buys = [o for o in orders if o.action == "BUY"]

        with self.db.session() as s:
            for o in sells:
                pos = s.query(PortfolioPosition).filter(
                    PortfolioPosition.code == o.code).first()
                if pos is None:
                    continue
                t = self._fill_sell(pos, date, o.reason)
                if t is None:
                    continue  # 跌停顺延：pending 指令保留，明日重试
                filled.append(t)
                s.add(t)
                s.delete(pos)
                st.unsettled += t.amount - t.fee
            s.commit()

        held = {p.code for p in self._positions()}
        with self.db.session() as s:
            for o in buys:
                if o.code in held:
                    continue
                equity = self._equity(date)
                t = self._fill_buy(o.code, date, equity * o.weight, st.cash)
                if t is None:
                    continue  # 涨停/现金不足顺延
                filled.append(t)
                s.add(t)
                st.cash -= t.amount + t.fee
                close = self._close(o.code, date) or t.price
                s.add(PortfolioPosition(
                    code=o.code, shares=t.shares, entry_price=t.price,
                    entry_date=date, highest_close=close,
                    weight=o.weight, score=0.0,
                    industry=industry_of(self.db, o.code)))
                held.add(o.code)
            s.commit()

        # 已执行的指令清除；未成交顺延者保留（明日 on_open 重试）
        filled_codes = {t.code for t in filled}
        with self.db.session() as s:
            for o in orders:
                if o in s:
                    if o.action == "SELL":
                        gone = s.query(PortfolioPosition).filter(
                            PortfolioPosition.code == o.code).first() is None
                        if gone and o.code not in filled_codes:
                            # 重复 SELL 指令（无持仓）清理
                            s.delete(o)
                        elif gone:
                            s.delete(o)
                    elif o.code in filled_codes:
                        s.delete(o)
            s.commit()
        return filled

    def _fill_buy(self, code: str, date: str, budget: float,
                  cash: float) -> PortfolioTrade | None:
        o, prev_close = self._open(code, date)
        if o is None:
            return None
        if prev_close is not None and matching.is_limit_up(o, prev_close, self.config):
            return None
        price = matching.buy_price(o, self.config)
        budget = min(budget, cash)
        shares = int(budget / price / 100) * 100
        if shares <= 0:
            return None
        amount = shares * price
        fee = matching.total_fee(amount, self.config, "buy")
        if amount + fee > cash and shares > 100:
            shares -= 100
            amount = shares * price
            fee = matching.total_fee(amount, self.config, "buy")
        return PortfolioTrade(code=code, action="BUY", exec_date=date, price=price,
                              shares=shares, amount=amount, fee=fee, reason="ENTRY")

    def _fill_sell(self, pos: PortfolioPosition, date: str,
                   reason: str) -> PortfolioTrade | None:
        o, prev_close = self._open(pos.code, date)
        if o is None:
            return None
        if prev_close is not None and matching.is_limit_down(o, prev_close, self.config):
            return None
        price = matching.sell_price(o, self.config)
        amount = pos.shares * price
        fee = matching.total_fee(amount, self.config, "sell")
        pnl = amount - fee - pos.shares * pos.entry_price - \
            matching.total_fee(pos.shares * pos.entry_price, self.config, "buy")
        return PortfolioTrade(code=pos.code, action="SELL", exec_date=date,
                              price=price, shares=pos.shares, amount=amount, fee=fee,
                              pnl=pnl, pnl_pct=(amount - fee) / (pos.shares * pos.entry_price) - 1.0,
                              reason=reason)

    # -- 收盘：估值 + 决策（复用 #22/#23） ------------------------------------

    def on_close(self, date: str):
        """收盘后：估值落 portfolio_nav，跑 daily_review 决策落待执行指令。"""
        st = self.state
        equity = self._equity(date)
        st.highest_nav = max(st.highest_nav, equity)

        with self.db.session() as s:
            if not s.query(PortfolioNav).filter(PortfolioNav.date == date).first():
                s.add(PortfolioNav(date=date, nav=equity / self.initial_capital))
            # 更新持仓 highest_close（移动止盈依赖）
            for p in s.query(PortfolioPosition).all():
                c = self._close(p.code, date)
                if c is not None:
                    p.highest_close = max(p.highest_close, c)
            s.commit()

        drawdown = (st.highest_nav - equity) / st.highest_nav if st.highest_nav else 0.0
        holdings = [
            exits.Holding(code=p.code, entry_price=p.entry_price,
                          entry_date=p.entry_date, highest_close=p.highest_close)
            for p in self._positions()
        ]
        signals = generate_entry_signals(self.db, date)
        sig_list = [
            sizing.SignalLike(
                code=code,
                level=normalize_signal_level(sig.level),
                industry=industry_of(self.db, code),
                score=100.0 + (10.0 if sig.level == SIGNAL_STRONG else 0.0))
            for code, sig in signals.items() if sig.entry_allowed
        ]
        holding_likes = [
            sizing.HoldingLike(code=p.code, industry=p.industry,
                               weight=p.shares * p.entry_price / max(equity, 1e-9),
                               score=p.score)
            for p in self._positions()
        ]
        return daily_review(self.db, date, self.config, equity,
                            holdings=holdings, signals=sig_list,
                            drawdown=drawdown, highest_nav=st.highest_nav,
                            nav=equity)

    # -- 连续运行 -------------------------------------------------------------

    def run(self, start: str, end: str) -> list[str]:
        dates = _trading_dates(self.db, start, end)
        for i, dt in enumerate(dates):
            if i > 0:
                self.on_open(dt)
            self.on_close(dt)
        return dates


def _review(db, date, config, equity, holdings, signals, holding_likes,
            drawdown, highest_nav):
    """收盘决策包装：daily_review 需要的组合状态。"""
    return daily_review(db, date, config, equity, holdings=holdings,
                        signals=signals, drawdown=drawdown,
                        highest_nav=highest_nav, nav=equity)


# -- 报告 -------------------------------------------------------------------

def paper_report(db: Database, initial_capital: float = INITIAL_CAPITAL) -> dict:
    """读回落表：净值曲线、指标（胜率/回撤/收益等）、当前持仓明细。"""
    with db.session() as s:
        navs = s.query(PortfolioNav).order_by(PortfolioNav.date).all()
        trades = s.query(PortfolioTrade).order_by(PortfolioTrade.exec_date).all()
        positions = s.query(PortfolioPosition).all()
    dates = [n.date for n in navs]
    nav = [n.nav for n in navs]
    closed = [t for t in trades if t.action == "SELL"]
    traded_value = sum(t.amount for t in trades)
    metrics = compute_metrics(
        nav=nav or [1.0], dates=dates or ["1970-01-01"], benchmark_nav=[1.0] * len(nav),
        trades=[{"pnl_pct": t.pnl_pct * 100} for t in closed],
        traded_value=traded_value, initial_capital=initial_capital)
    return {
        "initial_capital": initial_capital,
        "nav": [{"date": d, "nav": v} for d, v in zip(dates, nav)],
        "metrics": metrics,
        "positions": [
            {"code": p.code, "shares": p.shares, "entry_price": p.entry_price,
             "entry_date": p.entry_date, "highest_close": p.highest_close,
             "weight": p.weight}
            for p in positions
        ],
        "trades": [
            {"code": t.code, "action": t.action, "exec_date": t.exec_date,
             "price": t.price, "shares": t.shares, "amount": t.amount,
             "fee": t.fee, "pnl": t.pnl, "pnl_pct": t.pnl_pct, "reason": t.reason}
            for t in trades
        ],
    }
