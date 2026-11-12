"""AkshareAdapter（主力，spec §3.1）：akshare 股票历史行情 → 标准化日线。"""
from __future__ import annotations

import pandas as pd

from app.data.base_adapter import BaseQuoteAdapter
from app.data.models import DailyBar


def _akshare_fetch(code: str, start: str, end: str) -> pd.DataFrame:
    """调用 akshare stock_zh_a_hist 获取历史行情。"""
    import akshare as ak

    return ak.stock_zh_a_hist(
        symbol=code,
        period="daily",
        start_date=start.replace("-", ""),
        end_date=end.replace("-", ""),
        adjust="qfq",  # 前复权
    )

# akshare stock_zh_a_hist 中文列名 → 英文标准名
_AKSHARE_COLUMNS = {
    "日期": "date",
    "股票代码": "code",
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",
    "成交额": "amount",
}


class AkshareAdapter(BaseQuoteAdapter):
    """akshare 东财行情适配器（代码格式：纯数字 600519）。

    未传入 fetch 时，使用内置的 akshare 真实数据源；传入 fetch 时用于测试 stub。
    """

    def __init__(
        self,
        fetch=None,
        cache_dir: str | None = None,
        retry_delays: list[float] | None = None,
    ):
        _fetch = fetch if fetch is not None else _akshare_fetch
        super().__init__(fetch=_fetch, cache_dir=cache_dir, retry_delays=retry_delays)

    def _to_source_code(self, code: str) -> str:
        return code.split(".")[0]

    def _convert(self, raw: pd.DataFrame, code: str) -> list[DailyBar]:
        df = raw.rename(columns=_AKSHARE_COLUMNS)
        missing = {"date", "open", "close", "high", "low", "volume", "amount"} - set(
            df.columns
        )
        if missing:
            raise ValueError(f"akshare 返回缺少必需列: {missing}")
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
                    volume=int(row["volume"]),
                    amount=float(row["amount"]),
                )
            )
        return bars
