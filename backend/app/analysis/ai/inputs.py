"""公告输入结构（data 层公告源接口的第一版最小约定）。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Announcement:
    """单条公告（原始全文）。"""

    code: str
    ann_date: str  # YYYY-MM-DD
    ann_type: str  # 年报/季报/重大事项…
    title: str
    content: str


class AnnouncementProvider(Protocol):
    """公告与交易日来源（测试用 stub 注入）。"""

    def announcements(self, code: str) -> list[Announcement]: ...

    def trading_day(self) -> str: ...
