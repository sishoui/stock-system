"""每日模拟盘编排（issue #26，spec §11/§12）：APScheduler cron 任务。

- paper_daily：交易日 17:10（数据增量任务 17:00 之后）收盘决策；
- paper_open：交易日 09:35 开盘虚拟成交；
- 非交易日由任务内部按 daily_bar 日历自行判断，静默跳过。
"""
from __future__ import annotations

from apscheduler.schedulers.base import BaseScheduler
from apscheduler.triggers.cron import CronTrigger

from sqlalchemy import text


def build_paper_scheduler(on_open, on_close, tz: str = "Asia/Shanghai",
                          sched: BaseScheduler | None = None) -> BaseScheduler:
    """注册每日模拟盘任务。on_open/on_close 为接收 ISO 日期字符串的可调用。"""
    sched = sched or __import__("apscheduler.schedulers.blocking",
                                fromlist=["BlockingScheduler"]).BlockingScheduler(timezone=tz)
    sched.add_job(_wrap(on_open), CronTrigger(hour=9, minute=35,
                                              day_of_week="mon-fri", timezone=tz),
                  id="paper_open", replace_existing=True)
    sched.add_job(_wrap(on_close), CronTrigger(hour=17, minute=10,
                                               day_of_week="mon-fri", timezone=tz),
                  id="paper_daily", replace_existing=True)
    return sched


def _wrap(fn):
    def job():
        from app.data.db import default_db_url
        from app.data.db import Database

        db = Database(default_db_url())
        d = _latest_trading_date(db)
        if d is None:
            return
        fn(d)
    return job


def _latest_trading_date(db) -> str | None:
    """最新交易日（<= 今天）；无数据返回 None。"""
    from datetime import date as _d

    with db.session() as s:
        v = s.execute(text("SELECT MAX(date) FROM daily_bar WHERE date <= :t"),
                      {"t": _d.today().isoformat()}).scalar()
    return str(v) if v else None


def default_paper_jobs():
    """默认任务实现：使用默认库与 RiskConfig。"""
    from app.common.config import RiskConfig
    from app.data.db import default_db_url
    from app.data.db import Database
    from app.backtest.paper import PaperTradingEngine

    db = Database(default_db_url())
    eng = PaperTradingEngine(db, RiskConfig())
    return eng.on_open, eng.on_close
