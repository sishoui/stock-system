"""股票池表 + 变更留痕表（issue #21，spec §5.3）。

独立 Base：池维护是策略层产物，不回写 data 层 schema。
"""
from __future__ import annotations

from sqlalchemy import Float, Index, Integer, String, Text

from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class PoolBase(DeclarativeBase):
    pass


class PoolMembership(PoolBase):
    """股票池成员：入池时间 + 当前池内综合分。"""

    __tablename__ = "pool_membership"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    entered_at: Mapped[str] = mapped_column(String(10), nullable=False)  # 入池日期
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    ai_sustainability: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str] = mapped_column(String(64), nullable=False, default="new_report")  # 入池原因

    __table_args__ = (Index("ix_pool_membership_code", "code"),)


class PoolChangeLog(PoolBase):
    """池变更留痕：enter/remove，含原因（AI fail、ST、暴雷、排名跌出等）。"""

    __tablename__ = "pool_change_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String(10), nullable=False)
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    action: Mapped[str] = mapped_column(String(8), nullable=False)  # enter / remove
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")

    __table_args__ = (
        Index("ix_pool_change_log_date", "date"),
        Index("ix_pool_change_log_code", "code"),
    )
