"""交易日历（spec §3.3）：akshare 交易日历判断，测试可注入 stub。

真实实现走 akshare.tool_trade_date_hist_sina；失败时降级为「周一至周五均交易日」。
"""
from __future__ import annotations

from datetime import date as _date, timedelta

from app.common.config import DataConfig


class TradeCalendar:
    """基于 akshare 交易日历的交易日判断。"""

    def __init__(self, fetch_trade_dates=None):
        self._fetch = fetch_trade_dates  # 注入点：() -> list[str]，形如 '2026-01-05'
        self._dates: set[str] | None = None

    def _load(self) -> set[str]:
        if self._dates is None:
            if self._fetch is not None:
                self._dates = set(self._fetch())
            else:
                try:
                    import akshare as ak

                    df = ak.tool_trade_date_hist_sina()
                    self._dates = {
                        d.isoformat() if hasattr(d, "isoformat") else str(d)
                        for d in df["trade_date"].tolist()
                    }
                except Exception:  # noqa: BLE001 网络不可用时降级
                    self._dates = None
            if self._dates is None:
                # 降级：回溯到 2000 年的周末剔除法，直到 2100 年
                d = _date(2000, 1, 1)
                out = set()
                while d < _date(2100, 1, 1):
                    if d.weekday() < 5:
                        out.add(d.isoformat())
                    d += timedelta(days=1)
                self._dates = out
        return self._dates

    def is_trading_day(self, date: str) -> bool:
        return date in self._load()

    def trading_days(self, start: str, end: str) -> list[str]:
        d0 = _date.fromisoformat(start)
        d1 = _date.fromisoformat(end)
        dates = self._load()
        out = []
        d = d0
        while d <= d1:
            s = d.isoformat()
            if s in dates:
                out.append(s)
            d += timedelta(days=1)
        return out


def default_data_config() -> DataConfig:
    return DataConfig()
