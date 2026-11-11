"""标准化日线行情模型（字段英文标准名，spec §3.1/§3.2）。"""
from __future__ import annotations

import re
from dataclasses import dataclass

_CODE_RE = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")


@dataclass(frozen=True)
class DailyBar:
    """单日行情（原始不复权）。"""

    code: str  # 600519.SH
    date: str  # YYYY-MM-DD
    open: float
    high: float
    low: float
    close: float
    volume: int
    amount: float


def normalize_code(code: str) -> str:
    """任意常见格式 → 600519.SH。

    支持：600519 / 600519.SH / sh.600519 / 600519.SH。
    """
    code = code.strip().lower()
    m = re.match(r"^(sh|sz|bj)[.\s]?(\d{6})$", code)
    if m:
        return f"{m.group(2)}.{m.group(1).upper()}"
    m = re.match(r"^(\d{6})[.\s]?(sh|sz|bj)?$", code)
    if m:
        suffix = m.group(2)
        if suffix is None:
            prefix = m.group(1)
            if prefix.startswith("6"):
                suffix = "sh"
            elif prefix.startswith(("4", "8")):
                suffix = "bj"
            else:
                suffix = "sz"
        return f"{m.group(1)}.{suffix.upper()}"
    raise ValueError(f"无法识别的股票代码格式: {code}")


def validate_code(code: str) -> str:
    if not _CODE_RE.match(code):
        raise ValueError(f"股票代码必须为 600519.SH 格式: {code}")
    return code
