"""适配器公共基类：代码转换、限速重试、本地落库缓存。

设计（spec §3.1）：
- fetch 依赖注入（构造参数），真实环境接 akshare/baostock SDK，测试传 stub。
- 缓存按 (code, start, end) 落 SQLite；命中即不再触发外部调用。
- 限速重试：失败按 retry_delays 退避重试，耗尽后抛出最后一个异常。
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from app.data.models import DailyBar, normalize_code


class QuoteCache:
    """SQLite 落库缓存：key = (code, start, end) → 序列化 bars。"""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS daily_bar_cache ("
            " code TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL,"
            " bars TEXT NOT NULL, PRIMARY KEY (code, start, end))"
        )

    def get(self, code: str, start: str, end: str) -> list[DailyBar] | None:
        row = self._conn.execute(
            "SELECT bars FROM daily_bar_cache WHERE code=? AND start=? AND end=?",
            (code, start, end),
        ).fetchone()
        if row is None:
            return None
        return [DailyBar(**d) for d in json.loads(row[0])]

    def put(self, code: str, start: str, end: str, bars: list[DailyBar]) -> None:
        payload = json.dumps([b.__dict__ for b in bars], ensure_ascii=False)
        self._conn.execute(
            "INSERT OR REPLACE INTO daily_bar_cache (code, start, end, bars)"
            " VALUES (?, ?, ?, ?)",
            (code, start, end, payload),
        )
        self._conn.commit()


class BaseQuoteAdapter:
    """QuoteSource 适配器骨架；子类实现 _to_source_code 与 _convert。"""

    default_retry_delays: list[float] = [5.0, 30.0, 120.0]

    def __init__(
        self,
        fetch=None,
        cache_dir: Path | str | None = None,
        retry_delays: list[float] | None = None,
    ):
        self._fetch = fetch
        self._retry_delays = retry_delays if retry_delays is not None else self.default_retry_delays
        self._cache = QuoteCache(Path(cache_dir) / "quote_cache.db") if cache_dir else None

    # -- 子类钩子 ---------------------------------------------------------

    def _to_source_code(self, code: str) -> str:
        """转换为该数据源的代码格式（如 600519 / sh.600519）。"""
        raise NotImplementedError

    def _convert(self, raw, code: str) -> list[DailyBar]:
        """原始 DataFrame → 标准化 DailyBar 列表（中文列名转换在此）。"""
        raise NotImplementedError

    # -- 公共流程 ---------------------------------------------------------

    def get_daily_bars(self, code: str, start: str, end: str) -> list[DailyBar]:
        code = normalize_code(code)
        if self._cache is not None:
            cached = self._cache.get(code, start, end)
            if cached is not None:
                return cached

        if self._fetch is None:
            raise RuntimeError(f"{type(self).__name__} 未配置 fetch 数据源")

        source_code = self._to_source_code(code)
        last_exc: Exception | None = None
        for i, delay in enumerate([*self._retry_delays, None]):
            if delay is not None and i > 0:
                time.sleep(delay)
            try:
                raw = self._fetch(source_code, start, end)
                break
            except Exception as exc:  # noqa: BLE001 限速/网络错误统一退避重试
                last_exc = exc
        else:
            raise last_exc  # type: ignore[misc]

        bars = self._convert(raw, code)
        if self._cache is not None:
            self._cache.put(code, start, end, bars)
        return bars
