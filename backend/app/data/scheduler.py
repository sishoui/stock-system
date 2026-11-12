"""APScheduler 任务编排（issue #17，spec §3.3）。

- daily_increment：交易日 17:00（cron）。
- finance_full_refresh：财报季 4/8/10 月 1 日 06:00 全量重拉（可空跑）。
- ai_pool_assess：交易日 17:20 AI 全池批量评估（run_pool，可空跑占位）。
- rule_engine_orders：交易日 17:40 规则引擎/指令生成（daily_review，可空跑占位）。
- 非交易日由任务内部日历判断跳过。
"""
from __future__ import annotations

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger


def build_scheduler(daily_job, finance_full_job,
                    ai_pool_job=None, rule_engine_job=None,
                    tz: str = "Asia/Shanghai") -> BlockingScheduler:
    """注册数据增量、财报重拉与（可选）AI/规则引擎任务。

    ai_pool_job / rule_engine_job 未提供时不注册对应任务（保持旧调用兼容）。
    """
    sched = BlockingScheduler(timezone=tz)
    sched.add_job(daily_job, CronTrigger(hour=17, minute=0, day_of_week="mon-fri", timezone=tz),
                  id="daily_increment", replace_existing=True)
    sched.add_job(finance_full_job, CronTrigger(month="4,8,10", day=1, hour=6, minute=0, timezone=tz),
                  id="finance_full_refresh", replace_existing=True)
    if ai_pool_job is not None:
        sched.add_job(ai_pool_job,
                      CronTrigger(hour=17, minute=20, day_of_week="mon-fri", timezone=tz),
                      id="ai_pool_assess", replace_existing=True)
    if rule_engine_job is not None:
        sched.add_job(rule_engine_job,
                      CronTrigger(hour=17, minute=40, day_of_week="mon-fri", timezone=tz),
                      id="rule_engine_orders", replace_existing=True)
    return sched


def default_ai_pool_job():
    """AI 全池批量评估（spec §4.4）：数据增量后对全池跑 run_pool。

    从 ai_credential 表读取 API Key / Base URL / Model，创建 OpenAIClient，
    对股票池内全部标的执行三判断评估。未配置或未启用时保持空跑占位。
    """
    from app.data.db import default_db_url, Database
    from app.data.db_models import AICredential, PoolMembership
    from app.analysis.ai.client import OpenAIClient
    from app.analysis.ai.inputs import AnnouncementProvider
    from app.analysis.ai.routing import run_pool
    from app.analysis.ai.cost import CostTracker

    db_url = default_db_url()
    if "://" not in db_url:
        db_url = f"sqlite:///{db_url}"
    db = Database(db_url)
    db.create_all()

    with db.session() as s:
        cred = s.query(AICredential).order_by(AICredential.id.desc()).first()
        codes = [m.code for m in s.query(PoolMembership).all()]

    if cred is None or not cred.is_active or not cred.api_key:
        return 0  # 未配置/未启用：空跑

    client = OpenAIClient(api_key=cred.api_key, base_url=cred.base_url, model=cred.model_name)

    class _AnnouncementProvider(AnnouncementProvider):
        """第一版无独立公告源：公告为空，交易日取最近日线日期。"""

        def announcements(self, code: str):
            return []

        def trading_day(self) -> str:
            from sqlalchemy import text
            with db.session() as sess:
                v = sess.execute(text("SELECT MAX(date) FROM daily_bar")).scalar()
            return str(v) if v else ""

    provider = _AnnouncementProvider()
    from app.common.config import AIConfig
    config = AIConfig()
    tracker = CostTracker(db, config)
    result = run_pool(db, codes, client, provider, config, cost_tracker=tracker)
    return len(result.assessed) + len(result.reviewed) + len(result.degraded)


def default_rule_engine_job():
    """规则引擎/指令生成占位：收盘决策由 paper_daily（scheduler/paper.py 17:10）
    经 daily_review 完成；此处为独立调度链路的空跑占位。"""
    return 0


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
