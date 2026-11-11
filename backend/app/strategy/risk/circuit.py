"""组合回撤熔断（spec §7.4）：回撤 >10% 暂停新开仓，收窄至 <5% 恢复。"""
from __future__ import annotations

from app.common.config import RiskConfig


class CircuitBreaker:
    """状态机：closed → open（回撤 > dd_circuit_break）→ closed（回撤 < dd_recover）。

    中间区间（recover ≤ dd < break）保持当前状态（滞回）。
    """

    def __init__(self, config: RiskConfig, open_: bool = False):
        self.config = config
        self.is_open = open_

    @property
    def allow_new_entry(self) -> bool:
        return not self.is_open

    def update(self, drawdown: float) -> str:
        """按最新组合回撤推进状态，返回 'open' / 'closed' / 'open_hysteresis'。"""
        if drawdown > self.config.dd_circuit_break:
            self.is_open = True
            return "open"
        if drawdown < self.config.dd_recover:
            self.is_open = False
            return "closed"
        return "open_hysteresis" if self.is_open else "closed_hysteresis"
