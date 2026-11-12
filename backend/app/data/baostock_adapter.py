"""BaostockAdapter（兜底，spec §3.1）：baostock 历史 K 线 → 标准化日线。"""
from __future__ import annotations

import pandas as pd

from app.data.base_adapter import BaseQuoteAdapter
from app.data.models import DailyBar


def _baostock_fetch(code: str, start: str, end: str) -> pd.DataFrame:
    """调用 baostock query_history_k_data_plus 获取历史行情。"""
    import baostock as bs

    bs.login()
    try:
        fields = "date,code,open,high,low,close,volume,amount"
        rs = bs.query_history_k_data_plus(
            code,
            fields,
            start_date=start,  # baostock 要求 yyyy-MM-dd 格式
            end_date=end,
            frequency="d",
            adjustflag="3",  # 前复权
        )
        if rs is None or rs.error_code != "0":
            raise ValueError(f"baostock 查询失败: {rs.error_msg if rs else 'None response'}")
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        df = pd.DataFrame(rows, columns=rs.fields) if rows else pd.DataFrame(columns=fields.split(","))
    finally:
        bs.logout()
    return df

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

    def __init__(self, cache_dir: str | None = None, retry_delays: list[float] | None = None):
        super().__init__(fetch=_baostock_fetch, cache_dir=cache_dir, retry_delays=retry_delays)

    def _to_source_code(self, code: str) -> str:
        num, suffix = code.split(".")
        return f"{suffix.lower()}.{num}"

    def get_profile(self, code: str) -> dict:
        """从 baostock query_stock_basic 获取基础信息（每次全量拉取，子代码再过滤）。"""
        import baostock as bs
        import pandas as pd

        bs.login()
        try:
            rs = bs.query_stock_basic()
            if rs is None or rs.error_code != "0":
                raise ValueError(
                    f"baostock 查询股票基本信息失败: {rs.error_msg if rs else 'None response'}"
                )
            rows = []
            while rs.next():
                rows.append(rs.get_row_data())
            df = pd.DataFrame(rows, columns=rs.fields)

            source_code = self._to_source_code(code)
            match = df[df["code"] == source_code]
            if match.empty:
                return {
                    "code": code,
                    "name": "",
                    "board": "",
                    "list_date": "",
                    "delist_date": None,
                    "is_st": False,
                    "status_history": "",
                }
            r = match.iloc[0]
            name = str(r.get("code_name", ""))
            ipo_date = str(r.get("ipoDate", "")) or ""
            out_date = str(r.get("outDate", "")) or None
            if out_date == "":
                out_date = None
            return {
                "code": code,
                "name": name,
                "board": "",  # baostock query_stock_basic 不提供行业分类
                "list_date": ipo_date,
                "delist_date": out_date,
                "is_st": "ST" in name or "*ST" in name,
                "status_history": f"{ipo_date} listed",
            }
        finally:
            bs.logout()

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
