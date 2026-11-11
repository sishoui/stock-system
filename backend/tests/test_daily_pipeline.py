"""交易日增量管线与四条校验基线测试（issue #17，spec §3.3/§3.4）。

全部 stub 数据 + 临时 SQLite。
Seam：
- TradeCalendar.is_trading_day / trading_days：交易日判断
- DailyIncrementPipeline.run(date)：增量拉取入库（非交易日跳过）
- four checks（completeness/suspension/cross/abnormal）→ data_check_log 留痕
- build_scheduler()：APScheduler 任务编排
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from app.data.checks import (
    abnormal_check,
    completeness_check,
    cross_check,
    suspension_check,
)
from app.data.db import Database
from app.data.db_models import DailyBar as ORMDailyBar
from app.data.models import DailyBar
from app.data.pipeline import DailyIncrementPipeline
from app.data.trading_calendar import TradeCalendar

CODES = ["600519.SH", "000001.SZ", "300750.SZ"]


def make_db(tmp_path: Path) -> Database:
    db = Database(f"sqlite:///{tmp_path/'t.db'}")
    db.create_all()
    return db


class StubSource:
    """可控 stub：按 (code, date) 生成行情；可注入缺口/异常。"""

    def __init__(self, base: float = 100.0):
        self.base = base
        self.omit: set[tuple[str, str]] = set()  # 制造缺口
        self.overrides: dict[tuple[str, str], DailyBar] = {}

    def bar(self, code: str, date: str, prev_close: float | None = None) -> DailyBar:
        close = self.base + int(date.replace("-", "")) % 7
        open_ = close * 0.99
        return DailyBar(
            code=code, date=date, open=round(open_, 2),
            high=round(close * 1.02, 2), low=round(open_ * 0.98, 2),
            close=round(close, 2), volume=1_000_000, amount=close * 1_000_000,
        )

    def get_daily_bars(self, code: str, start: str, end: str) -> list[DailyBar]:
        out = []
        from datetime import date as _d, timedelta
        d0 = _d.fromisoformat(start)
        d1 = _d.fromisoformat(end)
        d = d0
        while d <= d1:
            ds = d.isoformat()
            key = (code, ds)
            if key not in self.omit:
                if key in self.overrides:
                    out.append(self.overrides[key])
                else:
                    out.append(self.bar(code, ds))
            d += timedelta(days=1)
        return out


class StubCalendar:
    """stub 交易日历：2026-01 周一至周五为交易日（简化）。"""

    def is_trading_day(self, date: str) -> bool:
        from datetime import date as _d
        d = _d.fromisoformat(date)
        return d.weekday() < 5

    def trading_days(self, start: str, end: str) -> list[str]:
        from datetime import date as _d, timedelta
        out = []
        d = _d.fromisoformat(start)
        while d <= _d.fromisoformat(end):
            if d.weekday() < 5:
                out.append(d.isoformat())
            d += timedelta(days=1)
        return out


def logs(db: Database, check_type: str | None = None) -> list:
    with db.session() as s:
        q = s.execute(
            text("SELECT check_type, stock_scope, result, alert_level, detail"
                 " FROM data_check_log" + (" WHERE check_type=:t" if check_type else "")
                 + " ORDER BY id"),
            {"t": check_type} if check_type else {},
        ).mappings().all()
        return [dict(r) for r in q]


def count_bars(db: Database, date: str) -> int:
    with db.session() as s:
        return s.query(ORMDailyBar).filter_by(date=date).count()


# ---------------------------------------------------------------- 交易日历

def test_calendar_weekend_not_trading_day():
    cal = StubCalendar()
    assert cal.is_trading_day("2026-01-05")  # 周一
    assert not cal.is_trading_day("2026-01-03")  # 周六


# ---------------------------------------------------------------- 增量管线

def test_pipeline_trading_day_incremental(tmp_path):
    db = make_db(tmp_path)
    src = StubSource()
    pipe = DailyIncrementPipeline(db, src, StubCalendar(), CODES)
    res = pipe.run("2026-01-05")
    assert res.bars == 3
    assert count_bars(db, "2026-01-05") == 3
    # 幂等：重复跑不重复入库
    res2 = pipe.run("2026-01-05")
    assert res2.bars == 0
    assert count_bars(db, "2026-01-05") == 3


def test_pipeline_skips_non_trading_day(tmp_path):
    db = make_db(tmp_path)
    pipe = DailyIncrementPipeline(db, StubSource(), StubCalendar(), CODES)
    res = pipe.run("2026-01-03")  # 周六
    assert res.skipped
    assert count_bars(db, "2026-01-03") == 0


def test_pipeline_catchup_refetches_missing(tmp_path):
    """缺口留痕 → 次日补抓：day1 某股缺数据，catchup 补齐。"""
    db = make_db(tmp_path)
    src = StubSource()
    src.omit.add(("600519.SH", "2026-01-05"))
    pipe = DailyIncrementPipeline(db, src, StubCalendar(), CODES)
    pipe.run("2026-01-05")
    missing = completeness_check(db, src, StubCalendar(), CODES, "2026-01-05")
    assert missing == ["600519.SH"]
    assert logs(db, "completeness")[0]["alert_level"] == "warn"

    # 次日：数据恢复，补抓
    src.omit.clear()
    pipe.run("2026-01-06", catchup_missing={"600519.SH": ["2026-01-05"]})
    with db.session() as s:
        assert s.query(ORMDailyBar).filter_by(code="600519.SH", date="2026-01-05").count() == 1


# ---------------------------------------------------------------- 校验

def _seed_day(db: Database, src: StubSource, date: str, codes=CODES):
    with db.session() as s:
        for c in codes:
            b = src.bar(c, date)
            s.add(ORMDailyBar(**b.__dict__))
        s.commit()


def test_completeness_no_gap_ok(tmp_path):
    db = make_db(tmp_path)
    src = StubSource()
    _seed_day(db, src, "2026-01-05")
    missing = completeness_check(db, src, StubCalendar(), CODES, "2026-01-05")
    assert missing == []
    assert logs(db, "completeness")[0]["result"] == "ok"


def test_suspension_not_false_positive(tmp_path):
    """停牌（volume=0 有记录）不算缺口、不误报为异常。"""
    db = make_db(tmp_path)
    src = StubSource()
    _seed_day(db, src, "2026-01-05", codes=CODES)
    with db.session() as s:
        row = s.query(ORMDailyBar).filter_by(code="000001.SZ", date="2026-01-05").one()
        row.volume = 0
        row.amount = 0.0
        s.commit()
    missing = completeness_check(db, src, StubCalendar(), CODES, "2026-01-05")
    assert missing == []
    suspended = suspension_check(db, "2026-01-05")
    assert suspended == ["000001.SZ"]
    entry = [l for l in logs(db, "suspension") if l["stock_scope"] == "000001.SZ"][0]
    assert entry["result"] == "ok"  # 停牌是正常状态，非告警


def test_cross_source_reconcile_warn_on_mismatch(tmp_path):
    db = make_db(tmp_path)
    src = StubSource()
    _seed_day(db, src, "2026-01-05", codes=CODES)
    # 第二源：000001.SZ 收盘价偏差 2% > 0.5% 阈值
    other = StubSource()
    b = other.bar("000001.SZ", "2026-01-05")
    b2 = DailyBar(**{**b.__dict__, "close": round(b.close * 1.02, 2)})
    other.overrides[("000001.SZ", "2026-01-05")] = b2
    mismatches = cross_check(
        db, other, sample_codes=CODES, watchlist=["300750.SZ"],
        date="2026-01-05", tolerance=0.005,
    )
    assert "000001.SZ" in mismatches
    assert logs(db, "cross")[0]["alert_level"] == "warn"


def test_abnormal_detection(tmp_path):
    db = make_db(tmp_path)
    src = StubSource()
    _seed_day(db, src, "2026-01-02")  # 前一日基准
    # 涨跌幅超限
    b = src.bar("600519.SH", "2026-01-05")
    prev = src.bar("600519.SH", "2026-01-02").close
    b1 = DailyBar(**{**b.__dict__, "close": round(prev * 1.6, 2), "high": max(b.high, round(prev * 1.6, 2))})  # +60%
    # 价格 <= 0
    b2 = DailyBar(**{**src.bar("000001.SZ", "2026-01-05").__dict__, "close": 0.0})
    # HLC 矛盾：high < low
    bb3 = src.bar("300750.SZ", "2026-01-05")
    b3 = DailyBar(**{**bb3.__dict__, "high": 10.0, "low": 12.0})
    with db.session() as s:
        s.add(ORMDailyBar(**b1.__dict__))
        s.add(ORMDailyBar(**b2.__dict__))
        s.add(ORMDailyBar(**b3.__dict__))
        s.commit()
    issues = abnormal_check(db, "2026-01-05", pct_limit=0.44)
    codes_flagged = {i[0] for i in issues}
    assert "600519.SH" in codes_flagged
    assert "000001.SZ" in codes_flagged
    assert "300750.SZ" in codes_flagged
    assert logs(db, "abnormal")[0]["alert_level"] == "error"


# ---------------------------------------------------------------- 调度编排

def test_scheduler_jobs_registered():
    from app.data.scheduler import build_scheduler
    sched = build_scheduler(
        daily_job=lambda: None, finance_full_job=lambda: None
    )
    jobs = {j.id: j for j in sched.get_jobs()}
    assert "daily_increment" in jobs
    assert "finance_full_refresh" in jobs
    trig = str(jobs["daily_increment"].trigger)
    assert "17" in trig
    ftrig = str(jobs["finance_full_refresh"].trigger)
    # 财报季 4/8/10 月
    assert all(m in ftrig for m in ("4", "8", "10"))
    sched.remove_all_jobs()


