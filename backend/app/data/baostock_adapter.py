"""BaostockAdapter（兜底，spec §3.1）：baostock 历史 K 线 → 标准化日线。"""
from __future__ import annotations

import pandas as pd

from app.data.base_adapter import BaseQuoteAdapter
from app.data.models import DailyBar

# baostock query_history_k_data_plus 列名本身即英文，值为字符串
_BAOSTOCK_COLUMNS = {
    "date": "date",
    "code": "code",
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "volume": "volume",
    "amount": "amount",
}


class BaostockAdapter(BaseQuoteAdapter):
    """baostock 适配器（代码格式：sh.600519）。"""

    def _to_source_code(self, code: str) -> str:
        num, suffix = code.split(".")
        return f"{suffix.lower()}.{num}"

    def _convert(self, raw: pd.DataFrame, code: str) -> list[DailyBar]:
        df = raw.rename(columns=_BAOSTOCK_COLUMNS)
        missing = {"date", "open", "close", "high", "low", "volume", "amount"} - set(
            df.columns
        )
        if missing:
            raise ValueError(f"baostock 返回缺少必需列: {missing}")
        bars = []
        for _, row in df.iterrows():
            bars.append(
                DailyBar(
                    code=code,
                    date=pd.Timestamp(row["date"]).strftime("%Y-%m-%d"),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=int(float(row["volume"])),
                    amount=float(row["amount"]),
                )
            )
        return bars
