"""数据源抽象协议（spec §3.1）。

按数据类别划分，可独立切换适配器：
- QuoteSource：日线行情
- FinanceSource：财务指标（本票仅定义协议，实现留后续票）
- ProfileSource：基础信息（板块/上市/退市/ST，同上）

统一约定：字段英文标准名；代码格式 `600519.SH`；
各适配器负责转换中文列名/字符串返回（在适配器内部完成）。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class QuoteSource(Protocol):
    """日线行情数据源协议。"""

    def get_daily_bars(
        self, code: str, start: str, end: str
    ) -> list["DailyBar"]:  # noqa: F821
        """返回 [start, end] 区间的标准化日线（不复权原始价）。"""
        ...


@runtime_checkable
class FinanceSource(Protocol):
    """财务指标数据源协议（实现留后续票）。"""

    def get_finance_indicators(self, code: str) -> list[dict]:
        ...


@runtime_checkable
class ProfileSource(Protocol):
    """基础信息数据源协议（实现留后续票）。"""

    def get_profile(self, code: str) -> dict:
        ...
