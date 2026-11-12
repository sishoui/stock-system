"""BaostockAdapter（兜底，spec §3.1）：baostock 历史 K 线 → 标准化日线。"""
from __future__ import annotations

import pandas as pd

from app.data.base_adapter import BaseQuoteAdapter
from app.data.models import DailyBar


def _to_float(val) -> float | None:
    """baostock 返回字符串，转 float；空/'None' 返回 None。"""
    if val is None or val == "" or val == "None":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


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

    def get_finance_indicators(self, code: str) -> list[dict]:
        """从 baostock 获取财务指标（近5年年报，quarter=4）。

        利润表 → roe/净利率/毛利率/净利润/营收
        资产负债表 → 资产负债率
        现金流表 → 经营现金流（CFOToOR × 营收）
        """
        import baostock as bs

        source_code = self._to_source_code(code)
        bs.login()
        results: list[dict] = []
        try:
            for year in range(2020, 2026):
                # 利润表
                rs = bs.query_profit_data(code=source_code, year=str(year), quarter="4")
                if rs is None or rs.error_code != "0":
                    continue
                profit_rows = []
                while rs.next():
                    profit_rows.append(rs.get_row_data())
                if not profit_rows:
                    continue
                profit = dict(zip(rs.fields, profit_rows[0]))

                # 资产负债表
                rs_b = bs.query_balance_data(code=source_code, year=str(year), quarter="4")
                balance = {}
                if rs_b and rs_b.error_code == "0":
                    b_rows = []
                    while rs_b.next():
                        b_rows.append(rs_b.get_row_data())
                    if b_rows:
                        balance = dict(zip(rs_b.fields, b_rows[0]))

                # 现金流量表
                rs_c = bs.query_cash_flow_data(code=source_code, year=str(year), quarter="4")
                cashflow = {}
                if rs_c and rs_c.error_code == "0":
                    c_rows = []
                    while rs_c.next():
                        c_rows.append(rs_c.get_row_data())
                    if c_rows:
                        cashflow = dict(zip(rs_c.fields, c_rows[0]))

                stat_date = profit.get("statDate", "")
                ann_date = profit.get("pubDate", "")
                if not stat_date:
                    continue

                mb_revenue = _to_float(profit.get("MBRevenue"))
                net_profit = _to_float(profit.get("netProfit"))
                cfo_to_or = _to_float(cashflow.get("CFOToOR"))
                operating_cashflow = (
                    cfo_to_or * mb_revenue if (cfo_to_or is not None and mb_revenue is not None)
                    else None
                )

                results.append({
                    "code": code,
                    "report_period": stat_date,
                    "ann_date": ann_date,
                    "roe": _to_float(profit.get("roeAvg")) * 100 if profit.get("roeAvg") else None,
                    "roe_ttm": None,
                    "revenue_growth": None,
                    "profit_growth": None,
                    "gross_margin": _to_float(profit.get("gpMargin")) * 100 if profit.get("gpMargin") else None,
                    "net_margin": _to_float(profit.get("npMargin")) * 100 if profit.get("npMargin") else None,
                    "debt_ratio": _to_float(balance.get("liabilityToAsset")) * 100 if balance.get("liabilityToAsset") else None,
                    "operating_cashflow": operating_cashflow,
                    "net_profit": net_profit,
                    "revenue": mb_revenue,
                })
        finally:
            bs.logout()
        return results

    def get_valuations(self, code: str, start: str, end: str) -> list[dict]:
        """从 baostock query_history_k_data_plus 获取估值数据（peTTM/pbMRQ）。"""
        import baostock as bs

        source_code = self._to_source_code(code)
        bs.login()
        results: list[dict] = []
        try:
            fields = "date,code,close,peTTM,pbMRQ"
            rs = bs.query_history_k_data_plus(
                source_code,
                fields,
                start_date=start,
                end_date=end,
                frequency="d",
                adjustflag="3",
            )
            if rs is None or rs.error_code != "0":
                return []
            while rs.next():
                row = rs.get_row_data()
                row_dict = dict(zip(rs.fields, row))
                date_val = row_dict.get("date", "")
                pe_ttm = _to_float(row_dict.get("peTTM"))
                pb = _to_float(row_dict.get("pbMRQ"))
                results.append({
                    "code": code,
                    "date": date_val,
                    "pe": pe_ttm,  # peTTM 映射到 pe 字段（Pydantic 只有 pe）
                    "pe_ttm": pe_ttm,
                    "pb": pb,
                    "dividend_yield": None,
                    "total_mv": None,
                })
        finally:
            bs.logout()
        return results

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
