"""QuoteSource 抽象协议与适配器测试（spec §3.1）。

Seam：`QuoteSource.get_daily_bars(code, start, end)` —— 统一英文标准名、
代码格式 600519.SH；适配器通过注入 fetch 依赖做 stub，不联网。
"""
from __future__ import annotations

import pandas as pd
import pytest

from app.data.sources import (
    FinanceSource,
    ProfileSource,
    QuoteSource,
)
from app.data.akshare_adapter import AkshareAdapter
from app.data.baostock_adapter import BaostockAdapter

CODE = "600519.SH"


def _akshare_frame() -> pd.DataFrame:
    """akshare stock_zh_a_hist 原始中文列名形态。"""
    return pd.DataFrame(
        {
            "日期": ["2024-01-02", "2024-01-03"],
            "股票代码": ["600519", "600519"],
            "开盘": [1700.0, 1710.0],
            "收盘": [1720.0, 1695.0],
            "最高": [1730.0, 1715.0],
            "最低": [1690.0, 1690.0],
            "成交量": [30000, 28000],
            "成交额": [5150000000.0, 4800000000.0],
        }
    )


def _baostock_frame() -> pd.DataFrame:
    """baostock query_history_k_data_plus 返回形态（字符串值）。"""
    return pd.DataFrame(
        {
            "date": ["2024-01-02", "2024-01-03"],
            "code": ["sh.600519", "sh.600519"],
            "open": ["1700.00", "1710.00"],
            "high": ["1730.00", "1715.00"],
            "low": ["1690.00", "1690.00"],
            "close": ["1720.00", "1695.00"],
            "volume": ["3000000", "2800000"],
            "amount": ["5150000000", "4800000000"],
        }
    )


class RecordingFetch:
    """可编程 fetch stub：记录调用并按脚本依次抛错/返回。"""

    def __init__(self, frame: pd.DataFrame, failures: int = 0):
        self.frame = frame
        self.failures = failures
        self.calls: list[tuple] = []

    def __call__(self, code: str, start: str, end: str) -> pd.DataFrame:
        self.calls.append((code, start, end))
        if self.failures > 0:
            self.failures -= 1
            raise ConnectionError("rate limited")
        return self.frame.copy()


class TestProtocols:
    def test_quote_source_protocol_exists(self):
        assert issubclass(AkshareAdapter, QuoteSource)
        assert issubclass(BaostockAdapter, QuoteSource)

    def test_finance_and_profile_are_protocol_only(self):
        # FinanceSource / ProfileSource 协议已定义；baostock 适配器已实现
        # get_finance_indicators / get_profile（真实数据回补），akshare 适配器
        # 继承 BaseQuoteAdapter 的空默认实现，均满足结构化子类型。
        assert issubclass(FinanceSource, object)
        assert issubclass(ProfileSource, object)
        assert issubclass(BaostockAdapter, FinanceSource)
        assert issubclass(BaostockAdapter, ProfileSource)
        # akshare 适配器仅继承 BaseQuoteAdapter 的空默认实现，
        # 同样满足结构化子类型（方法存在即视为实现）。
        assert issubclass(AkshareAdapter, FinanceSource)
        assert issubclass(AkshareAdapter, ProfileSource)


class TestAkshareAdapter:
    def test_returns_standardized_bars(self):
        fetch = RecordingFetch(_akshare_frame())
        adapter = AkshareAdapter(fetch=fetch)
        bars = adapter.get_daily_bars(CODE, "2024-01-01", "2024-01-31")

        assert len(bars) == 2
        bar = bars[0]
        assert bar.code == "600519.SH"
        assert bar.date == "2024-01-02"
        assert bar.open == 1700.0
        assert bar.close == 1720.0
        assert bar.high == 1730.0
        assert bar.low == 1690.0
        assert bar.volume == 30000
        assert bar.amount == 5150000000.0

    def test_converts_code_format_for_source(self):
        fetch = RecordingFetch(_akshare_frame())
        adapter = AkshareAdapter(fetch=fetch)
        adapter.get_daily_bars(CODE, "2024-01-01", "2024-01-31")
        # akshare 使用纯数字代码
        assert fetch.calls[0][0] == "600519"


class TestBaostockAdapter:
    def test_returns_standardized_bars_from_string_frame(self):
        fetch = RecordingFetch(_baostock_frame())
        adapter = BaostockAdapter(fetch=fetch)
        bars = adapter.get_daily_bars(CODE, "2024-01-01", "2024-01-31")

        assert len(bars) == 2
        bar = bars[1]
        assert bar.code == "600519.SH"
        assert bar.date == "2024-01-03"
        assert bar.close == 1695.0
        assert bar.volume == 2800000

    def test_converts_code_format_for_source(self):
        fetch = RecordingFetch(_baostock_frame())
        adapter = BaostockAdapter(fetch=fetch)
        adapter.get_daily_bars(CODE, "2024-01-01", "2024-01-31")
        # baostock 使用 sh.600519 格式
        assert fetch.calls[0][0] == "sh.600519"


class TestRetryAndRateLimit:
    def test_retries_on_failure_then_succeeds(self):
        fetch = RecordingFetch(_akshare_frame(), failures=2)
        adapter = AkshareAdapter(
            fetch=fetch, retry_delays=[0.0, 0.0]
        )  # 测试不真等退避
        bars = adapter.get_daily_bars(CODE, "2024-01-01", "2024-01-31")
        assert len(bars) == 2
        assert len(fetch.calls) == 3

    def test_exhausted_retries_raise(self):
        fetch = RecordingFetch(_akshare_frame(), failures=5)
        adapter = AkshareAdapter(fetch=fetch, retry_delays=[0.0, 0.0])
        with pytest.raises(ConnectionError):
            adapter.get_daily_bars(CODE, "2024-01-01", "2024-01-31")


class TestCache:
    def test_second_call_hits_cache_not_network(self, tmp_path):
        fetch = RecordingFetch(_akshare_frame())
        adapter = AkshareAdapter(fetch=fetch, cache_dir=tmp_path)
        first = adapter.get_daily_bars(CODE, "2024-01-01", "2024-01-31")
        second = adapter.get_daily_bars(CODE, "2024-01-01", "2024-01-31")

        assert len(fetch.calls) == 1  # 第二次未触发外部调用
        assert second == first

    def test_different_range_refetches(self, tmp_path):
        fetch = RecordingFetch(_akshare_frame())
        adapter = AkshareAdapter(fetch=fetch, cache_dir=tmp_path)
        adapter.get_daily_bars(CODE, "2024-01-01", "2024-01-31")
        adapter.get_daily_bars(CODE, "2024-02-01", "2024-02-28")
        assert len(fetch.calls) == 2
