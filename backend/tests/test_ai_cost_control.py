"""AI 成本控制与分层路由（issue #19，spec §4.4）。

全 stub 客户端 + 临时 SQLite。
Seam：
- run_pool(db, codes, client, provider, config)：全池批量评估编排——
  预算检查 → 指纹防重评 → 便宜模型批量 → suspect/低置信/拟操作升级 reasoner 复核
- CostTracker：ai_cost_log 记账 + 日/月预算检查
- FailureWindowTracker：连续 N 个调度窗口失败 → ERROR 告警
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from app.analysis.ai.client import LLMResponse
from app.analysis.ai.cost import CostTracker
from app.analysis.ai.inputs import Announcement
from app.analysis.ai.prompts import build_inputs
from app.analysis.ai.routing import FailureWindowTracker, run_pool
from app.analysis.ai.service import get_ai_factor
from app.common.config import AIConfig
from app.data.db import Database
from app.data.db_models import AiAssessment, AiCostLog, FinanceIndicator, DataCheckLog

CODE = "600519.SH"
MODEL = "deepseek-chat"
REASONER = "deepseek-reasoner"

VALID_OUTPUT = {
    "authenticity": {"verdict": "pass", "confidence": 0.9, "reasons": ["ROE 稳定"]},
    "timeliness": {"valid_until": "2026-06-30", "decay_hint": "slow", "reasons": ["年报"]},
    "sustainability": {"score": 5, "trend": "improving", "key_risks": []},
    "evidence": [{"source": "财报/招股书段落", "quote": "营收 10 亿"}],
}


def make_db(tmp_path: Path) -> Database:
    db = Database(f"sqlite:///{tmp_path / 't.db'}")
    db.create_all()
    return db


def seed(db: Database) -> None:
    with db.session() as s:
        s.add(FinanceIndicator(
            code=CODE, report_period="2025-12-31", ann_date="2026-03-28",
            roe=15.0, revenue=1e10, net_profit=5e8, gross_margin=0.3,
            operating_cashflow=6e8,
        ))
        s.add(DataCheckLog(
            check_date="2026-03-30", check_type="cross", stock_scope=CODE,
            result="warn", alert_level="warn", detail="偏差 0.02",
        ))
        s.commit()


class StubProvider:
    def announcements(self, code: str) -> list[Announcement]:
        return [
            Announcement(
                code=code, ann_date="2026-03-28", ann_type="年报",
                title="2025 年度报告", content="本公司 2025 年营收 100 亿元。",
            ),
        ]

    def trading_day(self) -> str:
        return "2026-03-30"


def cfg(**kw) -> AIConfig:
    return AIConfig(**kw)


def costs(db: Database) -> list[AiCostLog]:
    with db.session() as s:
        return s.query(AiCostLog).all()


def latest_row(db: Database) -> AiAssessment:
    with db.session() as s:
        return (
            s.query(AiAssessment)
            .order_by(AiAssessment.id.desc())
            .first()
        )


class ModelClient:
    """complete(prompt, model) 的 stub；按模型返回可配置 JSON。"""

    def __init__(self, payload=None, cost=0.01):
        self.payload = payload or VALID_OUTPUT
        self.cost = cost
        self.calls: list[str] = []  # 记录 (model)

    def complete(self, prompt: str, model: str) -> LLMResponse:
        self.calls.append(model)
        if isinstance(self.payload, Exception):
            raise self.payload
        return LLMResponse(
            content=json.dumps(self.payload, ensure_ascii=False),
            model=model, tokens_in=100, tokens_out=50, cost_cny=self.cost,
        )


class BrokenClient:
    """永远抛异常的客户端（测退避与告警）。"""

    def __init__(self, cost=0.01):
        self.cost = cost
        self.calls: list[str] = []

    def complete(self, prompt: str, model: str) -> LLMResponse:
        self.calls.append(model)
        raise RuntimeError("api down")


# --------------------------------------------------------------- 成本预算

def test_cost_tracker_records_each_call(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    tracker = CostTracker(db, cfg())
    tracker.record("2026-03-30", CODE, MODEL, tokens_in=100, tokens_out=50, cost_cny=0.01)
    tracker.record("2026-03-30", CODE, REASONER, tokens_in=200, tokens_out=80, cost_cny=0.05)
    rows = costs(db)
    assert len(rows) == 2
    assert rows[0].tokens_in == 100 and rows[0].cost_cny == pytest.approx(0.01)
    assert rows[1].model == REASONER


def test_daily_and_monthly_spent(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    tracker = CostTracker(db, cfg())
    tracker.record("2026-03-30", CODE, MODEL, 1, 1, 3.0)
    tracker.record("2026-03-30", "000001.SZ", MODEL, 1, 1, 2.0)
    tracker.record("2026-02-28", CODE, MODEL, 1, 1, 100.0)
    assert tracker.daily_spent("2026-03-30") == pytest.approx(5.0)
    assert tracker.monthly_spent("2026-03-30") == pytest.approx(5.0)  # 2 月不计入 3 月
    assert tracker.monthly_spent("2026-02-28") == pytest.approx(100.0)


def test_budget_exhausted_marks_remaining_not_assessed(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    seed(db)
    client = ModelClient(cost=10.0)  # 每个标的 10 元，日限 10
    config = cfg(daily_cost_limit=10.0, monthly_budget=200.0)
    result = run_pool(db, [CODE, "000001.SZ"], client, StubProvider(), config)
    # 第一个花了 6 元，第二个预算不足 → 未评估，不再调用
    assert client.calls == [MODEL]
    assert result.not_assessed_budget == ["000001.SZ"]
    row = latest_row(db)
    assert row.code == "000001.SZ"
    assert json.loads(row.payload_json)["reason"] == "budget_exhausted"


def test_monthly_budget_also_blocks(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    seed(db)
    tracker = CostTracker(db, cfg())
    tracker.record("2026-03-01", CODE, MODEL, 1, 1, 201.0)
    client = ModelClient(cost=5.0)
    result = run_pool(db, [CODE], client, StubProvider(), cfg(),
                      cost_tracker=tracker)
    assert result.not_assessed_budget == [CODE]
    assert client.calls == []


# --------------------------------------------------------------- 分层路由

def test_suspect_upgrades_to_reasoner_review(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    seed(db)
    suspect = json.loads(json.dumps(VALID_OUTPUT))
    suspect["authenticity"]["verdict"] = "suspect"
    client = ModelClient(payload=suspect, cost=0.01)
    config = cfg(review_suspect=True)
    run_pool(db, [CODE], client, StubProvider(), config)
    assert client.calls == [MODEL, REASONER]  # 批量便宜模型 + 复核 reasoner
    row = latest_row(db)
    assert row.model_version == REASONER
    assert row.review_round == 1
    payload = json.loads(row.payload_json)
    assert payload["review_round"] == 1
    assert payload["review_model"] == REASONER


def test_low_confidence_triggers_review(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    seed(db)
    low = json.loads(json.dumps(VALID_OUTPUT))
    low["authenticity"]["confidence"] = 0.5
    client = ModelClient(payload=low)
    run_pool(db, [CODE], client, StubProvider(), cfg())
    assert client.calls == [MODEL, REASONER]


def test_high_confidence_pass_no_review(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    seed(db)
    client = ModelClient()  # pass / 0.9
    run_pool(db, [CODE], client, StubProvider(), cfg())
    assert client.calls == [MODEL]  # 不复核


def test_must_review_codes_force_reasoner(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    seed(db)
    client = ModelClient()  # pass / 0.9，但当日拟操作
    run_pool(db, [CODE], client, StubProvider(), cfg(), must_review={CODE})
    assert client.calls == [MODEL, REASONER]
    assert latest_row(db).review_round == 1


def test_review_overwrites_conclusion(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    seed(db)
    suspect = json.loads(json.dumps(VALID_OUTPUT))
    suspect["authenticity"]["verdict"] = "suspect"
    reviewed = json.loads(json.dumps(VALID_OUTPUT))
    reviewed["authenticity"]["verdict"] = "fail"
    client = ModelClient(payload=suspect)
    # reasoner 轮返回不同结论
    class TwoStage(ModelClient):
        def complete(self, prompt, model):
            if model == REASONER:
                self.payload = reviewed
            return super().complete(prompt, model)

    client = TwoStage(payload=suspect)
    run_pool(db, [CODE], client, StubProvider(), cfg())
    row = latest_row(db)
    assert row.authenticity == "fail"  # 复核覆盖原结论
    assert get_ai_factor(db, CODE)["authenticity"] == "fail"


# --------------------------------------------------------------- 指纹防重评

def test_fingerprint_unchanged_skips(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    seed(db)
    client = ModelClient()
    run_pool(db, [CODE], client, StubProvider(), cfg())
    result = run_pool(db, [CODE], client, StubProvider(), cfg())
    assert client.calls == [MODEL]  # 第二轮不调用
    assert result.skipped_fingerprint == [CODE]


def test_model_switch_triggers_full_reassessment(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    seed(db)
    client = ModelClient()
    run_pool(db, [CODE], client, StubProvider(), cfg())
    assert client.calls == [MODEL]
    run_pool(db, [CODE], client, StubProvider(), cfg(cheap_model="glm-4-flash"))
    assert client.calls[-1] == "glm-4-flash"  # 模型切换 → 重评


def test_finance_change_triggers_reassessment(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    seed(db)
    client = ModelClient()
    run_pool(db, [CODE], client, StubProvider(), cfg())
    with db.session() as s:
        row = s.query(FinanceIndicator).filter_by(code=CODE).first()
        row.roe = 99.0  # 财报变化
        s.commit()
    result = run_pool(db, [CODE], client, StubProvider(), cfg())
    assert result.skipped_fingerprint == []
    assert len(client.calls) == 2


# --------------------------------------------------------------- 退避重试

def test_backoff_sequence_and_degrade(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    seed(db)
    client = BrokenClient()
    sleeps: list[float] = []
    config = cfg(backoff_seconds=[5, 30, 120])
    result = run_pool(db, [CODE], client, StubProvider(), config,
                      sleeper=sleeps.append)
    assert client.calls == [MODEL] * 4  # 1 次 + 3 次退避重试
    assert sleeps == [5, 30, 120]
    assert result.degraded == [CODE]
    assert latest_row(db).authenticity == "pending"


def test_cost_logged_even_on_failure(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    seed(db)
    # 失败也消耗 token：BrokenClient 不产生 response，改用坏 JSON 客户端
    class BadJsonClient:
        def __init__(self):
            self.calls = []

        def complete(self, prompt, model):
            self.calls.append(model)
            return LLMResponse(content="不是JSON", model=model,
                               tokens_in=100, tokens_out=10, cost_cny=0.02)

    client = BadJsonClient()
    run_pool(db, [CODE], client, StubProvider(), cfg(), sleeper=lambda _: None)
    rows = costs(db)
    assert len(rows) == 4
    assert sum(r.cost_cny for r in rows) == pytest.approx(0.08)


# --------------------------------------------------------------- 失败告警

def test_consecutive_window_failures_alert(caplog, tmp_path: Path) -> None:
    db = make_db(tmp_path)
    seed(db)
    tracker = FailureWindowTracker(cfg(consecutive_failure_alert=3))
    client = BrokenClient()
    with caplog.at_level(logging.ERROR, logger="app.analysis.ai"):
        run_pool(db, [CODE], client, StubProvider(), cfg(consecutive_failure_alert=3),
                 window_tracker=tracker, sleeper=lambda _: None)
        run_pool(db, [CODE], client, StubProvider(), cfg(consecutive_failure_alert=3),
                 window_tracker=tracker, sleeper=lambda _: None)
        assert not any(r.levelno == logging.ERROR for r in caplog.records)
        run_pool(db, [CODE], client, StubProvider(), cfg(consecutive_failure_alert=3),
                 window_tracker=tracker, sleeper=lambda _: None)
    assert any("连续 3 个调度窗口" in r.getMessage() for r in caplog.records
               if r.levelno == logging.ERROR)


def test_window_success_resets_counter(caplog, tmp_path: Path) -> None:
    db = make_db(tmp_path)
    seed(db)
    tracker = FailureWindowTracker(cfg(consecutive_failure_alert=3))
    with caplog.at_level(logging.ERROR, logger="app.analysis.ai"):
        tracker.record_window(ok=False)
        tracker.record_window(ok=False)
        tracker.record_window(ok=True)  # 成功重置
        tracker.record_window(ok=False)
        tracker.record_window(ok=False)
        assert not any(r.levelno == logging.ERROR for r in caplog.records)


# --------------------------------------------------------------- 默认配置

def test_ai_config_defaults_match_spec() -> None:
    c = AIConfig()
    assert c.daily_cost_limit == 10.0
    assert c.monthly_budget == 200.0
    assert c.cheap_model == "deepseek-chat"
    assert c.reasoner_model == "deepseek-reasoner"
