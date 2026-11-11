"""数据管线 CLI：交易日增量（issue #17）与历史回补（issue #16）。

用法：
    python -m app.data.cli --mode increment [--date 2026-01-05] --source stub
    python -m app.data.cli --mode backfill --start 2020 --end 2024 --source stub
"""
from __future__ import annotations

import argparse

from app.data.backfill import BackfillPipeline, StubDataSource
from app.data.db import Database, default_db_url
from app.data.models import normalize_code


def build_source(name: str):
    if name == "stub":
        return StubDataSource()
    if name in ("akshare", "baostock"):
        from app.data.akshare_adapter import AkshareAdapter
        from app.data.baostock_adapter import BaostockAdapter

        return AkshareAdapter() if name == "akshare" else BaostockAdapter()
    raise ValueError(f"未知数据源: {name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="数据管线 CLI：增量/回补")
    parser.add_argument("--mode", default="increment", choices=["increment", "backfill"])
    parser.add_argument("--date", help="增量日期，默认今天")
    parser.add_argument("--db", default=default_db_url(), help="SQLAlchemy URL 或 sqlite 文件路径")
    parser.add_argument("--start", type=int, help="回补起始年")
    parser.add_argument("--end", type=int, help="回补结束年")
    parser.add_argument("--source", default="stub", help="stub|akshare|baostock")
    parser.add_argument("--codes", default="600519.SH,000001.SZ", help="逗号分隔股票代码")
    args = parser.parse_args(argv)

    db_url = args.db if "://" in args.db else f"sqlite:///{args.db}"
    db = Database(db_url)
    db.create_all()
    source = build_source(args.source)
    codes = [normalize_code(c) for c in args.codes.split(",")]

    if args.mode == "backfill":
        pipeline = BackfillPipeline(db=db, source=source, codes=codes)
        result = pipeline.run_full(args.start, args.end)
        print(f"回补完成 [{args.start}-{args.end}] bars={result.bars}")
        return 0

    from datetime import date as _date

    from app.data.pipeline import DailyIncrementPipeline
    from app.data.trading_calendar import TradeCalendar

    date = args.date or _date.today().isoformat()
    pipe = DailyIncrementPipeline(db, source, TradeCalendar(), codes)
    res = pipe.run(date)
    print(f"增量 {date}: skipped={res.skipped} bars={res.bars} "
          f"catchup={res.catchup_bars} alerts={res.alerts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
