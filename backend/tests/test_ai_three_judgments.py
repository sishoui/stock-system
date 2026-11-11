"""AI 三判断（issue #18，spec §4.2/§4.3）。

全部 stub LLM 客户端 + 临时 SQLite。
Seam：
- build_inputs(db, code, provider)：单股第一版输入组装（财务 + 校验标记 + 公告全文/时效/趋势）
- assess(db, code, client, provider)：一次调用三判断合一 JSON，Pydantic 强校验，
  不合规重试一次后降级「未评估」，落 ai_assessment
- fingerprint(inputs, model_version)：输入数据指纹
- get_ai_factor(db, code)：规则引擎按普通因子读取
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.analysis.ai.client import LLMResponse
from app.analysis.ai.inputs import Announcement
from app.analysis.ai.prompts import build_inputs
from app.analysis.ai.schemas import ThreeJudgmentOutput
from app.analysis.ai.service import assess, fingerprint, get_ai_factor
from app.data.db import Database
from app.data.db_models import (
    AiAssessment,
    DataCheckLog,
    FinanceIndicator,
)

CODE = "600519.SH"
MODEL = "deepseek-chat"

VALID_OUTPUT = {
    "authenticity": {"verdict": "pass", "confidence": 0.9, "reasons": ["ROE 稳定"]},
    "timeliness": {"valid_until": "2026-06-30", "decay_hint": "slow", "reasons": ["年报"]},
    "sustainability": {"score": 5, "trend": "improving", "key_risks": []},
    "evidence": [{"source": "公告/财报字段", "quote": "营收 10 亿"}],
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
        s.add(FinanceIndicator(
            code=CODE, report_period="2025-09-30", ann_date="2025-10-25",
            roe=12.0, revenue=7e9, net_profit=3e8, gross_margin=0.28,
            operating_cashflow=4e8,
        ))
        s.add(DataCheckLog(
            check_date="2026-03-30", check_type="cross", stock_scope=CODE,
            result="warn", alert_level="warn", detail="对账偏差 0.02",
        ))
        s.commit()


class StubProvider:
    """公告与交易日 stub。"""

    def announcements(self, code: str) -> list[Announcement]:
        return [
            Announcement(
                code=code, ann_date="2026-03-28", ann_type="年报",
                title="2025 年度报告", content="本公司 2025 年营收 100 亿元……",
            ),
        ]

    def trading_day(self) -> str:
        return "2026-03-30"


class OkClient:
    """返回合法 JSON 的 stub 客户端。"""

    calls = 0
    last_prompt = ""
    payload = VALID_OUTPUT

    def complete(self, prompt: str) -> LLMResponse:
        type(self).calls += 1
        type(self).last_prompt = prompt
        return LLMResponse(
            content=json.dumps(self.payload, ensure_ascii=False),
            model=MODEL, tokens_in=100, tokens_out=50, cost_cny=0.01,
        )


class BadClient:
    """始终返回不合规输出的 stub 客户端。"""

    calls = 0

    def complete(self, prompt: str) -> LLMResponse:
        type(self).calls += 1
        return LLMResponse(
            content="我觉得这家公司不错", model=MODEL,
            tokens_in=100, tokens_out=10, cost_cny=0.01,
        )


class RecoverClient:
    """第一次不合规、第二次合法——验证只重试一次且能恢复。"""

    calls = 0

    def complete(self, prompt: str) -> LLMResponse:
        type(self).calls += 1
        content = "抱歉" if type(self).calls == 1 else json.dumps(VALID_OUTPUT, ensure_ascii=False)
        return LLMResponse(content=content, model=MODEL,
                           tokens_in=100, tokens_out=10, cost_cny=0.01)


# --------------------------------------------------------------- 输入组装

def test_build_inputs_shape(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    seed(db)
    inputs = build_inputs(db, CODE, StubProvider())
    # 结构化财务字段
    assert inputs["code"] == CODE
    assert inputs["finance"][-1]["roe"] == 15.0
    # 管线校验标记
    assert inputs["check_flags"] == [
        {"type": "cross", "result": "warn", "detail": "对账偏差 0.02"}
    ]
    # 原始公告全文
    assert inputs["announcements"][0]["content"].startswith("本公司")
    # 时效输入
    assert inputs["timeliness"] == {
        "announcement_date": "2026-03-28",
        "announcement_type": "年报",
        "trading_day": "2026-03-30",
    }
    # 可持续性输入：近 N 期趋势序列
    assert [p["report_period"] for p in inputs["trend"]] == ["2025-09-30", "2025-12-31"]


def test_build_inputs_empty_db(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    inputs = build_inputs(db, CODE, StubProvider())
    assert inputs["finance"] == []
    assert inputs["check_flags"] == []
    assert inputs["trend"] == []


# --------------------------------------------------------------- 输出契约

def test_contract_rejects_bad_verdict() -> None:
    bad = json.loads(json.dumps(VALID_OUTPUT, ensure_ascii=False))
    bad["authenticity"]["verdict"] = "maybe"
    with pytest.raises(Exception):
        ThreeJudgmentOutput.model_validate(bad)


def test_contract_rejects_out_of_range_score() -> None:
    bad = json.loads(json.dumps(VALID_OUTPUT, ensure_ascii=False))
    bad["sustainability"]["score"] = 11
    with pytest.raises(Exception):
        ThreeJudgmentOutput.model_validate(bad)


# --------------------------------------------------------------- 评估落库

def test_assess_ok_writes_row(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    seed(db)
    OkClient.calls = 0
    record = assess(db, CODE, OkClient(), StubProvider(), model_version=MODEL)
    assert OkClient.calls == 1
    with db.session() as s:
        row = s.query(AiAssessment).order_by(AiAssessment.id.desc()).first()
    assert row is not None
    assert row.code == CODE
    assert row.authenticity == "pass"
    assert row.authenticity_confidence == pytest.approx(0.9)
    assert row.timeliness_valid_until == "2026-06-30"
    assert row.sustainability_score == 5
    assert row.sustainability_trend == "improving"
    assert row.model_version == MODEL
    assert row.data_fingerprint  # 非空指纹
    payload = json.loads(row.payload_json)
    assert payload["authenticity"]["verdict"] == "pass"
    assert record.degraded is False


def test_assess_prompt_contains_inputs(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    seed(db)
    OkClient.calls = 0
    OkClient.last_prompt = ""
    assess(db, CODE, OkClient(), StubProvider(), model_version=MODEL)
    assert "600519.SH" in OkClient.last_prompt
    assert "2025 年度报告" in OkClient.last_prompt


def test_assess_degrades_after_retry(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    seed(db)
    BadClient.calls = 0
    record = assess(db, CODE, BadClient(), StubProvider(), model_version=MODEL)
    assert BadClient.calls == 2  # 恰好一次重试
    assert record.degraded is True
    with db.session() as s:
        row = s.query(AiAssessment).order_by(AiAssessment.id.desc()).first()
    assert row.authenticity == "pending"  # 「未评估」
    assert row.timeliness_valid_until is None
    assert row.sustainability_score is None


def test_assess_recovers_on_second_call(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    seed(db)
    RecoverClient.calls = 0
    record = assess(db, CODE, RecoverClient(), StubProvider(), model_version=MODEL)
    assert RecoverClient.calls == 2
    assert record.degraded is False


# --------------------------------------------------------------- 指纹

def test_fingerprint_stable_and_sensitive(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    seed(db)
    inputs = build_inputs(db, CODE, StubProvider())
    fp1 = fingerprint(inputs, MODEL)
    fp2 = fingerprint(inputs, MODEL)
    assert fp1 == fp2
    other_model = fingerprint(inputs, "glm-4-flash")
    assert fp1 != other_model
    db2 = make_db(tmp_path / "b")
    db2.create_all()
    seed_other = Database(f"sqlite:///{tmp_path / 'b2.db'}")
    seed_other.create_all()
    with seed_other.session() as s:
        s.add(FinanceIndicator(code=CODE, report_period="2025-12-31",
                               ann_date="2026-03-28", roe=9.0))
        s.commit()
    inputs2 = build_inputs(seed_other, CODE, StubProvider())
    assert fingerprint(inputs2, MODEL) != fp1


# --------------------------------------------------------------- 因子读取

def test_get_ai_factor_reads_latest(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    seed(db)
    # 未评估：返回中性因子
    factor = get_ai_factor(db, CODE)
    assert factor["status"] == "not_assessed"
    assert factor["authenticity"] == "pending"
    assert factor["sustainability_score"] == 0.0
    # 评估后：返回结论
    OkClient.calls = 0
    assess(db, CODE, OkClient(), StubProvider(), model_version=MODEL)
    factor = get_ai_factor(db, CODE)
    assert factor["status"] == "ok"
    assert factor["authenticity"] == "pass"
    assert factor["valid_until"] == "2026-06-30"
    assert factor["sustainability_score"] == 5.0
    assert factor["model_version"] == MODEL
