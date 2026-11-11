"""API 响应模型（spec §9）：所有含 AI 结论的模型复用三判断 schema。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.analysis.ai.schemas import ThreeJudgmentOutput


class NavSparkline(BaseModel):
    dates: list[str]
    nav: list[float]


class SystemStatus(BaseModel):
    data_health: Literal["ok", "warn", "error", "idle"]
    ai_health: Literal["ok", "degraded", "idle"]
    last_check_date: str | None = None
    alerts: list[str] = Field(default_factory=list)


class ActionSummary(BaseModel):
    """今日行动摘要：可买/卖出/观察。"""

    date: str
    buy_candidates: list[str] = Field(default_factory=list)
    sell_orders: list[str] = Field(default_factory=list)
    watch: list[str] = Field(default_factory=list)
    circuit_open: bool = False


class DashboardResponse(BaseModel):
    action_summary: ActionSummary
    nav_sparkline: NavSparkline
    system_status: SystemStatus


class KLineBar(BaseModel):
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    ma20: float | None = None


class PoolItem(BaseModel):
    code: str
    name: str
    entered_at: str
    rank: int
    score: float
    ai: ThreeJudgmentOutput | None = None
    ai_status: Literal["ok", "not_assessed"] = "not_assessed"
    signal_level: Literal["strong", "medium", "weak"]
    signal_reasons: list[str] = Field(default_factory=list)


class PoolResponse(BaseModel):
    total: int
    items: list[PoolItem]


class ValuationInfo(BaseModel):
    date: str
    pe: float | None = None
    pe_ttm: float | None = None
    pb: float | None = None
    pe_percentile: float | None = None
    pb_percentile: float | None = None


class FinanceInfo(BaseModel):
    report_period: str
    roe: float | None = None
    roe_ttm: float | None = None
    revenue_growth: float | None = None
    profit_growth: float | None = None
    gross_margin: float | None = None
    debt_ratio: float | None = None
    net_profit: float | None = None
    revenue: float | None = None


class SignalInfo(BaseModel):
    level: Literal["strong", "medium", "weak"]
    trend: bool
    volume_confirm: bool
    breakout: bool
    ai: Literal["positive", "neutral", "negative"]
    reasons: list[str] = Field(default_factory=list)


class StockDetailResponse(BaseModel):
    code: str
    name: str
    kline: list[KLineBar]
    valuation: ValuationInfo
    finance: FinanceInfo | None = None
    ai: ThreeJudgmentOutput | None = None
    ai_status: Literal["ok", "not_assessed"]
    signal: SignalInfo


class PositionItem(BaseModel):
    code: str
    name: str | None = None
    shares: int
    entry_price: float
    entry_date: str
    market_value: float
    pnl: float
    pnl_pct: float
    weight: float
    stop_line: float | None = None
    trail_line: float | None = None
    stop_distance_pct: float | None = None


class PositionsResponse(BaseModel):
    total: int
    items: list[PositionItem]


class OrderItem(BaseModel):
    code: str
    action: Literal["BUY", "SELL", "REPLACE"]
    exec_date: str
    weight: float
    reason: str
    stop_line: float | None = None


class OrdersResponse(BaseModel):
    total: int
    items: list[OrderItem]


class TradeItem(BaseModel):
    code: str
    action: Literal["BUY", "SELL"]
    exec_date: str
    price: float
    shares: int
    amount: float
    fee: float
    pnl: float
    pnl_pct: float
    reason: str


class TradesResponse(BaseModel):
    total: int
    items: list[TradeItem]


class RiskConfigParam(BaseModel):
    name: str
    default: float
    min: float | None = None
    max: float | None = None
    description: str


class RiskConfigResponse(BaseModel):
    readonly: Literal[True] = True
    params: list[RiskConfigParam]


class BacktestRequest(BaseModel):
    codes: list[str] | None = None
    start: str
    end: str
    initial_capital: float = 200_000.0


class BacktestSummary(BaseModel):
    id: int
    status: Literal["running", "done", "failed"]
    start: str
    end: str
    initial_capital: float


class NavSeries(BaseModel):
    dates: list[str]
    nav: list[float]
    benchmark: list[float]


class TradeRecord(BaseModel):
    code: str
    entry_date: str
    entry_price: float
    exit_date: str | None
    exit_price: float | None
    shares: int
    pnl: float
    pnl_pct: float
    reason: str


class BacktestReportResponse(BacktestSummary):
    nav: NavSeries
    metrics: dict
    trades: list[TradeRecord]
