"""一次性历史回补 CLI（issue #16）。

用法：
    python -m app.data.cli --db data/stock.db --start 2020 --end 2024 \
        --source stub --codes 600519.SH,000001.SZ

--source stub 使用内置 stub 数据源（离线/测试）；--source akshare/baostock
走 #15 适配器（需联网）。
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
        # 真实适配器在 #15 提供；日线外的类别由适配器后续补齐
        from app.data.akshare_adapter import AkshareAdapter
        from app.data.baostock_adapter import BaostockAdapter

        return AkshareAdapter() if name == "akshare" else BaostockAdapter()
    raise ValueError(f"未知数据源: {name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="全市场历史数据回补")
    parser.add_argument("--db", default=default_db_url(), help="SQLAlchemy URL 或 sqlite 文件路径")
    parser.add_argument("--start", type=int, required=True, help="起始年份")
    parser.add_argument("--end", type=int, required=True, help="结束年份")
    parser.add_argument("--source", default="stub", help="stub|akshare|baostock")
    parser.add_argument(
        "--codes",
        default="600519.SH,000001.SZ",
        help="逗号分隔的股票代码（全市场回补时传入完整清单）",
    )
    args = parser.parse_args(argv)

    db_url = args.db if "://" in args.db else f"sqlite:///{args.db}"
    db = Database(db_url)
    db.create_all()
    source = build_source(args.source)
    pipeline = BackfillPipeline(db=db, source=source, codes=[normalize_code(c) for c in args.codes.split(",")])
    result = pipeline.run_full(args.start, args.end)
    print(
        f"回补完成 [{args.start}-{args.end}] "
        f"日线 {result.bars} 条，估值/财务/基础信息已入库"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
