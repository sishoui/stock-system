"""成本记账与预算检查（spec §4.4）。

每次 LLM 调用（含失败重试消耗的 token）记 ai_cost_log；
每次调用前检查日/月累计花费，超限则拒绝评估。
"""
from __future__ import annotations

from app.common.config import AIConfig
from app.data.db import Database
from app.data.db_models import AiCostLog


class CostTracker:
    """ai_cost_log 记账 + 日/月预算检查。"""

    def __init__(self, db: Database, config: AIConfig) -> None:
        self._db = db
        self._config = config

    @staticmethod
    def _month(date: str) -> str:
        return date[:7]  # YYYY-MM

    def record(
        self, date: str, stock_code: str | None, model: str,
        tokens_in: int, tokens_out: int, cost_cny: float,
    ) -> None:
        with self._db.session() as s:
            s.add(AiCostLog(
                date=date, stock_code=stock_code, model=model,
                tokens_in=tokens_in, tokens_out=tokens_out, cost_cny=cost_cny,
            ))
            s.commit()

    def daily_spent(self, date: str) -> float:
        with self._db.session() as s:
            rows = s.query(AiCostLog).filter(AiCostLog.date == date).all()
        return sum(r.cost_cny for r in rows)

    def monthly_spent(self, date: str) -> float:
        month = self._month(date)
        with self._db.session() as s:
            rows = s.query(AiCostLog).filter(AiCostLog.date.like(f"{month}%")).all()
        return sum(r.cost_cny for r in rows)

    def budget_exhausted(self, date: str) -> bool:
        """日/月任一超限即 true：剩余标的标「未评估」。"""
        return (
            self.daily_spent(date) >= self._config.daily_cost_limit
            or self.monthly_spent(date) >= self._config.monthly_budget
        )
