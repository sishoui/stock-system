"""APScheduler 任务编排（issue #17，spec §3.3）。

- daily_increment：交易日 17:00（cron）。
- finance_full_refresh：财报季 4/8/10 月 1 日 06:00 全量重拉（可空跑）。
- 非交易日由管线内部日历判断跳过。
"""
from __future__ import annotations

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger


def build_scheduler(daily_job, finance_full_job, tz: str = "Asia/Shanghai") -> BlockingScheduler:
    sched = BlockingScheduler(timezone=tz)
    sched.add_job(daily_job, CronTrigger(hour=17, minute=0, day_of_week="mon-fri", timezone=tz),
                  id="daily_increment", replace_existing=True)
    sched.add_job(finance_full_job, CronTrigger(month="4,8,10", day=1, hour=6, minute=0, timezone=tz),
                  id="finance_full_refresh", replace_existing=True)
    return sched


def default_finance_full_job():
    """财报季财务全量重拉（可空跑）：当前仅有 stub 数据源时为 no-op。"""
    from app.data.db import default_db_url

    db_url = default_db_url()
    if "://" not in db_url:
        db_url = f"sqlite:///{db_url}"
    try:
        from app.data.backfill import BackfillPipeline, StubDataSource

        db = Database = None  # noqa: F841 - 空跑占位，接入真实财务源后实现
    except Exception:  # noqa: BLE001
        pass
    return 0
