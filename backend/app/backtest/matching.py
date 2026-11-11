"""撮合口径（spec §8.2）：成交价、费用、涨跌停判定。

- 买入成交价 = 开盘价 × (1 + 滑点)；卖出成交价 = 开盘价 × (1 - 滑点)
- 费用 = 佣金（双边，最低 5 元/笔）+ 印花税（仅卖出）
- 开盘价触及涨跌停视为不可成交，顺延
"""
from __future__ import annotations

from app.common.config import RiskConfig


def buy_price(open_: float, config: RiskConfig) -> float:
    return open_ * (1 + config.slippage)


def sell_price(open_: float, config: RiskConfig) -> float:
    return open_ * (1 - config.slippage)


def commission(amount: float, config: RiskConfig) -> float:
    return max(amount * config.commission_rate, config.min_commission)


def stamp_tax(amount: float, config: RiskConfig) -> float:
    return amount * config.stamp_tax


def total_fee(amount: float, config: RiskConfig, side: str) -> float:
    fee = commission(amount, config)
    if side == "sell":
        fee += stamp_tax(amount, config)
    return fee


def is_limit_up(open_: float, prev_close: float, config: RiskConfig) -> bool:
    return open_ >= prev_close * (1 + config.limit_pct) - 1e-9


def is_limit_down(open_: float, prev_close: float, config: RiskConfig) -> bool:
    return open_ <= prev_close * (1 - config.limit_pct) + 1e-9
