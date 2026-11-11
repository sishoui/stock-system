"""事件驱动日线回测引擎（spec §8.1/§8.2）。

节奏与实盘一致：信号日收盘决策 → 次日开盘成交。
- 复用 #22 signals.generate_entry_signals 生成入场信号；
- 复用 #23 exits.evaluate_exit / sizing.allocate / circuit.CircuitBreaker 生成出场与建仓计划；
- 撮合经 backtest.matching：滑点、费用、涨跌停不可成交顺延；
- 卖出资金 T+1 可用（当日开盘卖出所得，次日开盘才可用于买入）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import text

from app.backtest import matching
from app.backtest.metrics import compute_metrics
from app.common.config import RiskConfig
from app.data.db import Database
from app.strategy.pool_models import PoolBase
from app.strategy.risk import exits, sizing
from app.strategy.risk.circuit import CircuitBreaker
from app.strategy.signals import generate_entry_signals

BENCHMARK_CODE = "000300.SH"


@dataclass
class Trade:
    code: str
    entry_date: str
    entry_price: float
    shares: int
    amount_buy: float
    fee_buy: float
    exit_date: str | None = None
    exit_price: float = 0.0
    amount_sell: float = 0.0
    fee_sell: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    reason: str = ""


@dataclass
class _Pos:
    code: str
    shares: int
    entry_price: float
    entry_date: str
    amount_buy: float
    fee_buy: float
    highest_close: float
    weight: float
    score: float
    industry: str = ""


@dataclass
class BacktestResult:
    nav: list[float] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)
    benchmark_nav: list[float] = field(default_factory=list)
    nav_points: list[tuple[str, float]] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def export_nav_csv(self) -> str:
        """净值曲线导出（vs 沪深 300）。"""
        lines = ["date,nav,benchmark"]
        for d, n, b in zip(self.dates, self.nav, self.benchmark_nav):
            lines.append(f"{d},{n:.6f},{b:.6f}")
        return "\n".join(lines)


class BacktestEngine:
    def __init__(self, db: Database, config: RiskConfig, initial_capital: float = 200_000.0,
                 start: str = "", end: str = "", benchmark_code: str = BENCHMARK_CODE):
        self.db = db
        self.config = config
        self.initial_capital = initial_capital
        self.start = start
        self.end = end
        self.benchmark_code = benchmark_code

    # -- 数据辅助 ----------------------------------------------------------

    def _trading_dates(self) -> list[str]:
        with self.db.session() as s:
            rows = s.execute(text(
                "SELECT DISTINCT date FROM daily_bar WHERE code=:c"
                " AND date BETWEEN :s AND :e ORDER BY date"
            ), {"c": self.benchmark_code, "s": self.start, "e": self.end}).all()
        return [str(r[0]) for r in rows]

    def _benchmark_series(self, dates: list[str]) -> dict[str, float]:
        if not dates:
            return {}
        with self.db.session() as s:
            rows = s.execute(text(
                "SELECT date, close FROM daily_bar WHERE code=:c AND date BETWEEN :s AND :e"
                " ORDER BY date"
            ), {"c": self.benchmark_code, "s": dates[0], "e": dates[-1]}).all()
        if not rows:
            return {}
        first = rows[0][1] or 1.0
        return {str(d): (c or first) / first for d, c in rows}

    def _close(self, code: str, dt: str) -> float | None:
        with self.db.session() as s:
            v = s.execute(text(
                "SELECT close FROM daily_bar WHERE code=:c AND date=:d"
            ), {"c": code, "d": dt}).scalar()
        return v

    def _open(self, code: str, dt: str) -> tuple[float | None, float | None]:
        with self.db.session() as s:
            row = s.execute(text(
                "SELECT open,"
                " (SELECT close FROM daily_bar p WHERE p.code=:c AND p.date<:d"
                "  ORDER BY p.date DESC LIMIT 1)"
                " FROM daily_bar WHERE code=:c AND date=:d"
            ), {"c": code, "d": dt}).first()
        return (row[0], row[1]) if row else (None, None)

    # -- 撮合 ----------------------------------------------------------------

    def _fill_buy(self, code: str, exec_date: str, budget: float,
                  available_cash: float) -> Trade | None:
        """次日开盘买入；涨停不成交（顺延由调用方保留 pending）。"""
        o, prev_close = self._open(code, exec_date)
        if o is None:
            return None
        if prev_close is not None and matching.is_limit_up(o, prev_close, self.config):
            return None
        price = matching.buy_price(o, self.config)
        budget = min(budget, available_cash)
        shares = int(budget / price / 100) * 100  # A 股整手
        if shares <= 0:
            return None
        amount = shares * price
        fee = matching.total_fee(amount, self.config, "buy")
        if amount + fee > available_cash and shares > 100:
            shares -= 100
            amount = shares * price
            fee = matching.total_fee(amount, self.config, "buy")
        return Trade(code=code, entry_date=exec_date, entry_price=price,
                     shares=shares, amount_buy=amount, fee_buy=fee)

    def _fill_sell(self, pos: _Pos, exec_date: str, reason: str) -> Trade | None:
        """次日开盘卖出；跌停不成交（顺延）。"""
        o, prev_close = self._open(pos.code, exec_date)
        if o is None:
            return None
        if prev_close is not None and matching.is_limit_down(o, prev_close, self.config):
            return None
        price = matching.sell_price(o, self.config)
        amount = pos.shares * price
        fee = matching.total_fee(amount, self.config, "sell")
        pnl = amount - fee - pos.amount_buy - pos.fee_buy
        return Trade(code=pos.code, entry_date=pos.entry_date, entry_price=pos.entry_price,
                     shares=pos.shares, amount_buy=pos.amount_buy, fee_buy=pos.fee_buy,
                     exit_date=exec_date, exit_price=price, amount_sell=amount,
                     fee_sell=fee, pnl=pnl,
                     pnl_pct=(amount - fee) / (pos.amount_buy + pos.fee_buy) - 1.0,
                     reason=reason)

    # -- 主循环 --------------------------------------------------------------

    def run(self) -> BacktestResult:
        PoolBase.metadata.create_all(self.db.engine)
        dates = self._trading_dates()
        res = BacktestResult()
        if len(dates) < 2:
            res.metrics = compute_metrics([1.0], [self.start or "1970-01-01"], [1.0],
                                          [], 0.0, self.initial_capital)
            return res

        bm = self._benchmark_series(dates)
        settled_cash = self.initial_capital  # T+1 已结算可用现金
        unsettled_cash = 0.0                 # 当日卖出所得，次日可用
        positions: dict[str, _Pos] = {}
        closed: list[Trade] = []
        pending_sells: dict[str, tuple[_Pos, str]] = {}   # code -> (pos, reason)，开盘顺延重试
        pending_buys: list[list] = []                     # [code, weight, equity_at_signal]
        highest_nav = self.initial_capital
        cb = CircuitBreaker(self.config)
        traded_value = 0.0

        for i, dt in enumerate(dates):
            # 1) 开盘：T+1 结算昨日卖出资金
            settled_cash += unsettled_cash
            unsettled_cash = 0.0

            # 2) 开盘：先卖后买（跌停顺延：未成交者留在 pending_sells 明日重试）
            still_pending: dict[str, tuple[_Pos, str]] = {}
            for code, (pos, reason) in pending_sells.items():
                t = self._fill_sell(pos, dt, reason)
                if t is None:
                    still_pending[code] = (pos, reason)
                    continue
                closed.append(t)
                unsettled_cash += t.amount_sell - t.fee_sell
                traded_value += t.amount_sell
            pending_sells = still_pending

            still_buys: list[list] = []
            for code, weight, equity0 in pending_buys:
                if code in positions:
                    continue  # 已成交持仓，丢弃重复 pending
                t = self._fill_buy(code, dt, equity0 * weight, settled_cash)
                if t is None:
                    still_buys.append([code, weight, equity0])  # 涨停/现金不足顺延
                    continue
                settled_cash -= t.amount_buy + t.fee_buy
                traded_value += t.amount_buy
                bar_close = self._close(code, dt) or t.entry_price
                positions[code] = _Pos(
                    code=code, shares=t.shares, entry_price=t.entry_price,
                    entry_date=dt, amount_buy=t.amount_buy, fee_buy=t.fee_buy,
                    highest_close=bar_close, weight=weight, score=0.0)
            pending_buys = still_buys

            # 3) 收盘：估值 + 决策（最后一天只估值不出新决策）
            equity = settled_cash + unsettled_cash + sum(
                p.shares * (self._close(c, dt) or p.entry_price)
                for c, p in positions.items()
            )
            highest_nav = max(highest_nav, equity)
            cb.update((highest_nav - equity) / highest_nav)

            res.dates.append(dt)
            res.nav.append(equity / self.initial_capital)
            res.nav_points.append((dt, equity))
            res.benchmark_nav.append(bm.get(dt, 1.0))

            if i >= len(dates) - 1:
                break

            # 出场决策（S1/S2/移动止盈）→ 次日开盘卖出
            for c, pos in list(positions.items()):
                close = self._close(c, dt)
                if close is None:
                    continue
                pos.highest_close = max(pos.highest_close, close)
                holding = exits.Holding(code=pos.code, entry_price=pos.entry_price,
                                        entry_date=pos.entry_date,
                                        highest_close=pos.highest_close)
                es = exits.evaluate_exit(self.db, holding, dt, self.config)
                if es is not None:
                    pending_sells[c] = (positions.pop(c), es.rule)

            # 入场决策（熔断打开时不开新仓）
            if not cb.is_open:
                held = set(positions) | {b[0] for b in pending_buys}
                signals = generate_entry_signals(self.db, dt)
                sig_list = [
                    sizing.SignalLike(
                        code=code,
                        level={"medium": "mid", "strong": "strong", "weak": "weak"}[sig.level],
                        industry="",
                        score=100.0 + (10.0 if sig.level == "strong" else 0.0))
                    for code, sig in signals.items() if sig.entry_allowed
                ]
                if sig_list:
                    holdings = [
                        sizing.HoldingLike(
                            code=c, industry=p.industry,
                            weight=p.shares * p.entry_price / max(equity, 1e-9),
                            score=p.score)
                        for c, p in positions.items()
                    ]
                    plan = sizing.allocate(equity, sig_list, holdings, self.config)
                    for p in plan:
                        if p.action == "REPLACE":
                            victim = min(holdings, key=lambda h: h.score, default=None)
                            if victim is not None and victim.code in positions:
                                pending_sells[victim.code] = (
                                    positions.pop(victim.code), "REPLACE_WEAK")
                        if p.code not in held:
                            pending_buys.append([p.code, p.weight, equity])
        # 循环已覆盖全部交易日（含最后一天的开盘执行与收盘估值）
        res.trades = closed

        res.metrics = compute_metrics(
            nav=res.nav, dates=res.dates, benchmark_nav=res.benchmark_nav,
            trades=[{"pnl_pct": t.pnl_pct * 100} for t in closed],
            traded_value=traded_value, initial_capital=self.initial_capital)
        return res
