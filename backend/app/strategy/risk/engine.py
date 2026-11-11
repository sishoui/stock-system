"""每日链路（spec §7）：收盘后计算 → 次日开盘执行的待执行指令清单。

- exits.evaluate_exit 触发 → SELL 指令（次日开盘，全仓）
- sizing.allocate 输出 → BUY 指令（熔断打开时全部跳过）
- 指令落库（risk_pending_order），可按执行日查询卖出线
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Integer, Float, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.common.config import RiskConfig
from app.data.db import Database
from app.strategy.risk import exits, sizing
from app.strategy.risk.circuit import CircuitBreaker


class RiskBase(DeclarativeBase):
    """策略层风控表，独立于 data 层 schema。"""


class PendingOrder(RiskBase):
    """次日开盘待执行指令（含卖出线，回测与实盘口径一致）。"""

    __tablename__ = "risk_pending_order"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_date: Mapped[str] = mapped_column(String(10), nullable=False)  # 计算日（收盘后）
    exec_date: Mapped[str] = mapped_column(String(10), nullable=False)  # 执行日（次日开盘）
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    action: Mapped[str] = mapped_column(String(8), nullable=False)  # BUY / SELL / REPLACE
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    reason: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    stop_line: Mapped[float | None] = mapped_column(Float, nullable=True)  # 卖出线
    distance_pct: Mapped[float | None] = mapped_column(Float, nullable=True)


@dataclass
class Order:
    code: str
    action: str
    reason: str
    exec_date: str
    weight: float = 0.0
    stop_line: float | None = None
    distance_pct: float | None = None


@dataclass
class PlanResult:
    date: str
    next_exec_date: str
    orders: list[Order]
    circuit_open: bool


def _next_trading_date(db: Database, date: str) -> str:
    from sqlalchemy import text

    with db.session() as s:
        row = s.execute(
            text("SELECT MIN(date) FROM daily_bar WHERE date > :d"), {"d": date}
        ).scalar()
    if row is None:
        raise ValueError(f"日历中无 {date} 之后的交易日，无法确定次日执行日")
    return row


def build_orders(db: Database, date: str, config: RiskConfig, capital: float,
                 circuit_breaker: CircuitBreaker, signals: list,
                 holdings: list, exit_signals: list) -> list[Order]:
    """纯函数式编排：先卖后买，熔断只拦新开仓不拦出场。"""
    exec_date = _next_trading_date(db, date)
    orders: list[Order] = []
    for es in exit_signals:
        orders.append(Order(es.code, "SELL", es.rule, exec_date,
                            stop_line=es.stop_line, distance_pct=es.distance_pct))
    if circuit_breaker.allow_new_entry:
        sells = {es.code for es in exit_signals}
        remaining_seats = config.max_positions - len(holdings) + len(sells)
        for p in sizing.allocate(capital, signals, holdings, config):
            if p.action == "REPLACE":
                victim = min((h for h in holdings if h.code not in sells),
                             key=lambda h: h.score, default=None)
                if victim is not None:
                    orders.append(Order(victim.code, "SELL", "REPLACE_WEAK",
                                        exec_date))
            if remaining_seats <= 0:
                break
            orders.append(Order(p.code, "BUY", f"ENTRY_{p.action}", exec_date,
                                weight=p.weight))
            remaining_seats -= 1
    return orders


def daily_review(db: Database, date: str, config: RiskConfig, capital: float,
                 holdings: list | None = None, signals: list | None = None,
                 drawdown: float = 0.0, highest_nav: float | None = None,
                 nav: float | None = None) -> PlanResult:
    """每日收盘后统一入口。holdings/signals 由组合状态与入场信号层（#22）提供。"""
    holdings = holdings or []
    signals = signals or []
    cb = CircuitBreaker(config)
    if drawdown:
        cb.update(drawdown)
    elif highest_nav and nav:
        cb.update((highest_nav - nav) / highest_nav)

    exit_signals = []
    for h in holdings:
        es = exits.evaluate_exit(db, h, date, config)
        if es is not None:
            exit_signals.append(es)

    orders = build_orders(db, date, config, capital, cb, signals,
                          holdings, exit_signals)

    exec_date = orders[0].exec_date if orders else _next_trading_date(db, date)
    RiskBase.metadata.create_all(db.engine)
    with db.session() as s:
        for o in orders:
            s.add(PendingOrder(created_date=date, exec_date=exec_date, code=o.code,
                               action=o.action, weight=o.weight, reason=o.reason,
                               stop_line=o.stop_line, distance_pct=o.distance_pct))
        s.commit()
    return PlanResult(date, exec_date, orders, cb.is_open)


def get_pending_orders(db: Database, exec_date: str) -> list[PendingOrder]:
    """次日开盘前查询待执行指令清单（含卖出线与距离）。"""
    with db.session() as s:
        return s.query(PendingOrder).filter(PendingOrder.exec_date == exec_date).all()
