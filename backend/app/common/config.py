"""配置收口：DataConfig / AIConfig / RiskConfig（YAML 加载 + Pydantic v2 校验）。

默认值独立来源：docs/spec.md §7.5（RiskConfig）、§4.4（AIConfig）、§12（DataConfig）。
百分比参数统一以小数表示（0.08 = 8%）。
"""
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, BaseModel as _BM, ConfigDict, Field, field_validator


def _load_yaml(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"配置文件不存在: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"配置文件顶层必须是映射: {path}")
    return data


def _load_into(model_cls: type[_BM], path: Path) -> _BM:
    return model_cls.model_validate(_load_yaml(path))


class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class RiskConfig(_Config):
    """出场/仓位/熔断参数单一收口（§7.5）。

    每参数：默认值 + 建议搜索区间（ge/le 即区间边界）。
    """

    atr_multiplier: float = Field(2.0, ge=1.5, le=3.0, description="S1 止损 ATR 倍数")
    atr_period: int = Field(14, ge=10, le=20, description="ATR 回看窗口")
    trail_atr_mult: float = Field(3.0, ge=2.0, le=4.0, description="移动止盈 ATR 倍数")
    trail_pct: float = Field(0.08, ge=0.05, le=0.12, description="移动止盈回撤比例")
    max_positions: int = Field(10, ge=6, le=15, description="最大持仓数")
    position_scale_strong: float = Field(1.0, ge=0.8, le=1.2, description="强信号仓位系数")
    position_scale_mid: float = Field(0.6, ge=0.4, le=0.8, description="中信号仓位系数")
    max_single_weight: float = Field(0.15, ge=0.10, le=0.20, description="单票仓位上限")
    max_industry_weight: float = Field(0.30, ge=0.20, le=0.40, description="同行业仓位上限")
    dd_circuit_break: float = Field(0.10, ge=0.08, le=0.15, description="熔断触发回撤")
    dd_recover: float = Field(0.05, ge=0.03, le=0.08, description="熔断恢复线")
    # 撮合口径（spec §8.2，参数进 RiskConfig）
    commission_rate: float = Field(0.00025, ge=0.0, le=0.01, description="佣金率（双边）")
    min_commission: float = Field(5.0, ge=0.0, description="单笔最低佣金（元）")
    stamp_tax: float = Field(0.0005, ge=0.0, le=0.01, description="印花税率（仅卖出）")
    slippage: float = Field(0.001, ge=0.0, le=0.05, description="固定滑点（按开盘价）")
    limit_pct: float = Field(0.10, ge=0.05, le=0.20, description="涨跌停幅度")
    # 初始资金单一来源（spec §11：起始资金 20 万）
    initial_capital: float = Field(200_000.0, ge=0.0, description="初始资金（元）")

    @field_validator("dd_recover")
    @classmethod
    def _recover_below_break(cls, v: float, info) -> float:
        break_ = info.data.get("dd_circuit_break")
        if break_ is not None and v >= break_:
            raise ValueError("dd_recover 必须小于 dd_circuit_break")
        return v


class AIConfig(_Config):
    """AI 成本与路由参数收口（§4.4）。"""

    cheap_model: str = Field("deepseek-chat", description="批量评估用便宜模型")
    reasoner_model: str = Field("deepseek-reasoner", description="复核用 reasoner 模型")
    daily_cost_limit: float = Field(10.0, ge=0, description="每日费用上限（元）")
    monthly_budget: float = Field(200.0, ge=0, description="月度预算（元）")
    max_concurrency: int = Field(4, ge=1, description="信号量限并发数")
    backoff_seconds: list[int] = Field(
        [5, 30, 120], description="指数退避重试序列（秒，3 次）"
    )
    review_confidence_threshold: float = Field(
        0.7, ge=0.0, le=1.0, description="置信度低于该值升级复核"
    )
    review_suspect: bool = Field(True, description="三判断含 suspect 时升级复核")
    force_review_on_buy: bool = Field(
        True, description="当日拟买入/加仓的标的前强制复核"
    )
    consecutive_failure_alert: int = Field(
        3, ge=1, description="连续 N 个调度窗口失败触发告警"
    )


class DataConfig(_Config):
    """数据源档位、限速与对账抽样规则（§3.3/§3.4/§12）。"""

    source_tier: Literal["free", "paid"] = Field(
        "free", description="数据源档位：免费（akshare+baostock）/ 付费（tushare）"
    )
    rate_limit_qps: float = Field(2.0, ge=1.0, description="数据源限速（每秒请求数）")
    reconcile_sample_size: int = Field(50, ge=1, description="多源对账随机抽样股票数")
    reconcile_price_tolerance: float = Field(
        0.005, ge=0.0, le=0.05, description="对账收盘价允许相对偏差"
    )
    check_anomaly_pct_limit: float = Field(
        0.44, ge=0.0, le=1.0, description="涨跌幅异常阈值（新股除外）"
    )
    new_stock_exempt_trading_days: int = Field(
        244, ge=0, description="上市未满 N 个交易日内涨跌幅检查豁免（默认上市首年）"
    )


def load_risk_config(path: Path) -> RiskConfig:
    return _load_into(RiskConfig, path)


def load_ai_config(path: Path) -> AIConfig:
    return _load_into(AIConfig, path)


def load_data_config(path: Path) -> DataConfig:
    return _load_into(DataConfig, path)
