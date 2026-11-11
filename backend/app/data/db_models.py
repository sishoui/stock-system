"""八张表列级 schema（spec §3.2）。原始不复权价 + 复权因子，查询时按需复权。"""
from __future__ import annotations

from sqlalchemy import (
    Boolean,

    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class DailyBar(Base):
    """日线行情（原始不复权）。"""

    __tablename__ = "daily_bar"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    date: Mapped[str] = mapped_column(String(10), nullable=False)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[int] = mapped_column(Integer, nullable=False)  # 停牌=0
    amount: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (
        UniqueConstraint("code", "date", name="uq_daily_bar_code_date"),
        Index("ix_daily_bar_code_date", "code", "date"),
        Index("ix_daily_bar_date", "date"),
    )


class AdjustFactor(Base):
    """复权因子：后复权价 = 原始价 × 因子（相对上市日基准=1）。"""

    __tablename__ = "adjust_factor"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    date: Mapped[str] = mapped_column(String(10), nullable=False)
    factor: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (
        UniqueConstraint("code", "date", name="uq_adjust_factor_code_date"),
        Index("ix_adjust_factor_code_date", "code", "date"),
    )


class StockProfile(Base):
    """基础信息，ST/退市等状态变更留痕（status_history 逐行追加）。"""

    __tablename__ = "stock_profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    board: Mapped[str] = mapped_column(String(32), nullable=False)  # 主板/创业板/科创板/北交所
    list_date: Mapped[str] = mapped_column(String(10), nullable=False)
    delist_date: Mapped[str | None] = mapped_column(String(10), nullable=True)  # 退市留痕
    is_st: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status_history: Mapped[str] = mapped_column(Text, nullable=False, default="")

    __table_args__ = (Index("ix_stock_profile_code", "code"),)


class FinanceIndicator(Base):
    """财务指标（报告期口径，免费档新浪字段为准）。"""

    __tablename__ = "finance_indicator"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    report_period: Mapped[str] = mapped_column(String(10), nullable=False)  # 报告期，如 2023-12-31
    ann_date: Mapped[str | None] = mapped_column(String(10), nullable=True)  # 披露日
    roe: Mapped[float | None] = mapped_column(Float, nullable=True)  # ROE(%)
    roe_ttm: Mapped[float | None] = mapped_column(Float, nullable=True)
    revenue_growth: Mapped[float | None] = mapped_column(Float, nullable=True)
    profit_growth: Mapped[float | None] = mapped_column(Float, nullable=True)
    gross_margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    debt_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)  # 资产负债率(%)
    operating_cashflow: Mapped[float | None] = mapped_column(Float, nullable=True)  # 元
    net_profit: Mapped[float | None] = mapped_column(Float, nullable=True)  # 元
    revenue: Mapped[float | None] = mapped_column(Float, nullable=True)  # 元

    __table_args__ = (
        UniqueConstraint("code", "report_period", name="uq_finance_code_period"),
        Index("ix_finance_indicator_code_period", "code", "report_period"),
    )


class Valuation(Base):
    """估值（乐咕源：PE/PB/股息率）。"""

    __tablename__ = "valuation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    date: Mapped[str] = mapped_column(String(10), nullable=False)
    pe: Mapped[float | None] = mapped_column(Float, nullable=True)
    pe_ttm: Mapped[float | None] = mapped_column(Float, nullable=True)
    pb: Mapped[float | None] = mapped_column(Float, nullable=True)
    dividend_yield: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_mv: Mapped[float | None] = mapped_column(Float, nullable=True)  # 总市值(元)
    __table_args__ = (
        UniqueConstraint("code", "date", name="uq_valuation_code_date"),
        Index("ix_valuation_code_date", "code", "date"),
    )


class AiAssessment(Base):
    """AI 三判断结论（spec §4）。"""

    __tablename__ = "ai_assessment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    assessed_at: Mapped[str] = mapped_column(String(10), nullable=False)  # 评估时点
    authenticity: Mapped[str] = mapped_column(String(16), nullable=False)  # pass/suspect/fail/pending
    authenticity_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    timeliness_valid_until: Mapped[str | None] = mapped_column(String(10), nullable=True)
    sustainability_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    sustainability_trend: Mapped[str | None] = mapped_column(String(16), nullable=True)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    data_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)  # 输入数据指纹
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    review_round: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        Index("ix_ai_assessment_code_time", "code", "assessed_at"),
        Index("ix_ai_assessment_fingerprint", "data_fingerprint"),
    )


class AiCostLog(Base):
    """AI 成本记账（spec §4.4）。"""

    __tablename__ = "ai_cost_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String(10), nullable=False)
    stock_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_cny: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    __table_args__ = (Index("ix_ai_cost_log_date", "date"),)


class DataCheckLog(Base):
    """数据校验留痕（spec §3.4）。"""

    __tablename__ = "data_check_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    check_date: Mapped[str] = mapped_column(String(10), nullable=False)  # 校验日期
    check_type: Mapped[str] = mapped_column(String(32), nullable=False)  # completeness/cross/abnormal
    stock_scope: Mapped[str] = mapped_column(String(256), nullable=False, default="ALL")
    result: Mapped[str] = mapped_column(String(16), nullable=False)  # ok/warn/error
    alert_level: Mapped[str] = mapped_column(String(16), nullable=False, default="info")  # info/warn/error
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")

    __table_args__ = (Index("ix_data_check_log_date_type", "check_date", "check_type"),)
