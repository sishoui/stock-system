"""组合层表（issue #25，spec §8.4/§9）：持仓、成交、净值、回测任务。

第一版以模拟盘口径展示；回测结果落 JSON 快照，供 /backtests/{id} 读取。
"""
from __future__ import annotations

from sqlalchemy import Float, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class PortfolioBase(DeclarativeBase):
    """展示层组合表，独立 schema。"""


class PortfolioPosition(PortfolioBase):
    """当前持仓快照。"""

    __tablename__ = "portfolio_position"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    shares: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    entry_date: Mapped[str] = mapped_column(String(10), nullable=False)
    highest_close: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    industry: Mapped[str] = mapped_column(String(32), nullable=False, default="")


class PortfolioTrade(PortfolioBase):
    """历史成交。"""

    __tablename__ = "portfolio_trade"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    action: Mapped[str] = mapped_column(String(8), nullable=False)  # BUY / SELL
    exec_date: Mapped[str] = mapped_column(String(10), nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    shares: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    fee: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    pnl_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    reason: Mapped[str] = mapped_column(String(32), nullable=False, default="")


class PortfolioNav(PortfolioBase):
    """组合净值曲线点（模拟盘）。"""

    __tablename__ = "portfolio_nav"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String(10), nullable=False, unique=True)
    nav: Mapped[float] = mapped_column(Float, nullable=False)


class BacktestRun(PortfolioBase):
    """回测任务：请求参数 + 结果快照。"""

    __tablename__ = "backtest_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    params_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    result_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[str] = mapped_column(String(32), nullable=False, default="")

    __table_args__ = (Index("ix_backtest_run_created", "created_at"),)
