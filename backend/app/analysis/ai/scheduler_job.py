"""AI 调度任务实现（spec §4.4）。

放在 analysis 层，避免 data 层向 analysis/strategy 层反向依赖。
data/scheduler.py 中的 default_ai_pool_job 通过延迟导入调用此处实现。
"""
from __future__ import annotations

import importlib

from app.common.config import AIConfig
from app.data.db import default_db_url, Database
from app.data.db_models import AICredential
from app.analysis.ai.client import OpenAIClient
from app.analysis.ai.cost import CostTracker
from app.analysis.ai.inputs import AnnouncementProvider
from app.analysis.ai.routing import run_pool


class _AnnouncementProvider(AnnouncementProvider):
    """第一版无独立公告源：公告为空，交易日取最近日线日期。"""

    def __init__(self, db: Database):
        self._db = db

    def announcements(self, code: str):
        return []

    def trading_day(self) -> str:
        from sqlalchemy import text
        with self._db.session() as sess:
            v = sess.execute(text("SELECT MAX(date) FROM daily_bar")).scalar()
        return str(v) if v else ""


def default_ai_pool_job() -> int:
    """AI 全池批量评估（spec §4.4）：数据增量后对全池跑 run_pool。

    从 ai_credential 表读取 API Key / Base URL / Model，创建 OpenAIClient，
    对股票池内全部标的执行三判断评估。未配置或未启用时保持空跑占位。
    """
    db_url = default_db_url()
    if "://" not in db_url:
        db_url = f"sqlite:///{db_url}"
    db = Database(db_url)
    db.create_all()
    # 动态导入 strategy 层模型，避免 analysis 层静态反向依赖
    pool_mod = importlib.import_module("app.strategy.pool_models")
    PoolBase = pool_mod.PoolBase
    PoolMembership = pool_mod.PoolMembership
    PoolBase.metadata.create_all(db.engine)

    with db.session() as s:
        cred = s.query(AICredential).order_by(AICredential.id.desc()).first()
        codes = [m.code for m in s.query(PoolMembership).all()]

    if cred is None or not cred.is_active or not cred.api_key:
        return 0  # 未配置/未启用：空跑

    client = OpenAIClient(api_key=cred.api_key, base_url=cred.base_url, model=cred.model_name)

    provider = _AnnouncementProvider(db)
    config = AIConfig()
    tracker = CostTracker(db, config)
    result = run_pool(db, codes, client, provider, config, cost_tracker=tracker)
    return len(result.assessed) + len(result.reviewed) + len(result.degraded)
