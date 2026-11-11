"""Issue #16：建表 + 历史回补 + 复权价查询。stub 数据源，不联网。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.data.backfill import BackfillPipeline, StubDataSource
from app.data.db import Database
from app.data.price_query import PriceQuery


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(f"sqlite:///{tmp_path / 'test.db'}")


@pytest.fixture
def source() -> StubDataSource:
    return StubDataSource()


@pytest.fixture
def pipeline(db: Database, source: StubDataSource) -> BackfillPipeline:
    return BackfillPipeline(db=db, source=source, codes=["600519.SH", "000001.SZ"])


class TestSchema:
    def test_eight_tables_exist(self, db: Database):
        db.create_all()
        with db.engine.connect() as conn:
            names = {
                r[0]
                for r in conn.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        expected = {
            "daily_bar",
            "adjust_factor",
            "stock_profile",
            "finance_indicator",
            "valuation",
            "ai_assessment",
            "ai_cost_log",
            "data_check_log",
        }
        assert expected <= names

    def test_wal_mode_enabled(self, db: Database):
        db.create_all()
        with db.engine.connect() as conn:
            mode = conn.exec_driver_sql("PRAGMA journal_mode").scalar()
        assert str(mode).lower() == "wal"

    def test_daily_bar_indexes_exist(self, db: Database):
        db.create_all()
        with db.engine.connect() as conn:
            rows = conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='daily_bar'"
            ).fetchall()
        idx_names = {r[0] for r in rows}
        assert any("code_date" in n for n in idx_names)

    def test_create_all_idempotent(self, db: Database):
        db.create_all()
        db.create_all()


class TestBackfill:
    def test_backfill_writes_daily_bars(self, pipeline: BackfillPipeline):
        count = pipeline.run(2023, 2023)
        assert count > 0
        rows = pipeline.db.query_daily_bars("600519.SH", "2023-01-03", "2023-12-29")
        assert len(rows) == 238  # stub: 20 bars/month；区间裁剪到 01-03~12-29
        bar = rows[0]
        assert bar.code == "600519.SH" and bar.open > 0 and bar.high >= bar.low

    def test_backfill_writes_all_five_data_kinds(self, pipeline: BackfillPipeline):
        pipeline.run(2023, 2023)
        session = pipeline.db.session()
        from app.data.db_models import (
            AdjustFactor,
            FinanceIndicator,
            StockProfile,
            Valuation,
        )

        assert session.query(AdjustFactor).count() > 0
        assert session.query(StockProfile).count() == 2
        assert session.query(FinanceIndicator).count() > 0
        assert session.query(Valuation).count() > 0
        session.close()

    def test_backfill_profiles_keep_st_and_delisted(self, pipeline: BackfillPipeline):
        pipeline.run(2023, 2023)
        profiles = pipeline.db.get_profiles()
        by_code = {p.code: p for p in profiles}
        assert by_code["000001.SZ"].is_st is True  # stub 标记 ST 留痕
        st_hist = by_code["000001.SZ"].status_history
        assert any("ST" in e for e in st_hist.splitlines())

    def test_backfill_skips_existing_dates(self, pipeline: BackfillPipeline):
        n1 = pipeline.run(2023, 2023)
        n2 = pipeline.run(2023, 2023)
        assert n1 > 0 and n2 == 0


class TestPriceQuery:
    def test_raw_prices(self, pipeline: BackfillPipeline):
        pipeline.run(2023, 2023)
        pq = PriceQuery(pipeline.db)
        rows = pq.get_prices("600519.SH", "2023-01-03", "2023-01-31")
        assert rows and rows[0].close > 0

    def test_qfq_hfq_consistent_with_factor(self, pipeline: BackfillPipeline):
        pipeline.run(2023, 2023)
        pq = PriceQuery(pipeline.db)
        raw = pq.get_prices("600519.SH", "2023-01-03", "2023-12-29")
        hfq = pq.get_prices("600519.SH", "2023-01-03", "2023-12-29", adjust="hfq")
        qfq = pq.get_prices("600519.SH", "2023-01-03", "2023-12-29", adjust="qfq")
        assert len(raw) == len(hfq) == len(qfq)
        # 后复权 = 原始 × 因子（相对最早基准=1 → 首日相同，之后只增不减相对原始）
        assert hfq[0].close == pytest.approx(raw[0].close, rel=1e-6)
        assert hfq[-1].close > raw[-1].close
        # 前复权：最新日 = 原始价，历史日 ≤ 原始价
        assert qfq[-1].close == pytest.approx(raw[-1].close, rel=1e-6)
        assert qfq[0].close < raw[0].close

    def test_invalid_adjust_raises(self, pipeline: BackfillPipeline):
        pipeline.run(2023, 2023)
        pq = PriceQuery(pipeline.db)
        with pytest.raises(ValueError):
            pq.get_prices("600519.SH", "2023-01-03", "2023-01-31", adjust="bad")
