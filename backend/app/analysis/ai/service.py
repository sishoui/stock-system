"""三判断服务：一次调用、Pydantic 校验、重试一次后降级、落库（spec §4.1/§4.3）。

规则引擎视角的因子读取：get_ai_factor(db, code)。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from pydantic import ValidationError

from app.data.db import Database
from app.data.db_models import AiAssessment

from .client import LLMClient
from .inputs import AnnouncementProvider
from .prompts import build_prompt, build_inputs


@dataclass
class AssessmentRecord:
    code: str
    degraded: bool
    fingerprint: str


def fingerprint(inputs: dict, model_version: str) -> str:
    """输入数据指纹 = 财务/校验/公告内容 hash + 模型版本（spec §4.4）。"""
    payload = json.dumps(
        {"inputs": inputs, "model_version": model_version},
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def assess(
    db: Database,
    code: str,
    client: LLMClient,
    provider: AnnouncementProvider,
    *,
    model_version: str,
    assessed_at: str | None = None,
    trend_periods: int = 8,
) -> AssessmentRecord:
    """一次 LLM 调用输出三判断合一 JSON；不合规重试一次后降级「未评估」。"""
    inputs = build_inputs(db, code, provider, trend_periods=trend_periods)
    prompt = build_prompt(inputs)
    fp = fingerprint(inputs, model_version)
    assessed_at = assessed_at or provider.trading_day()

    parsed = None
    for _ in range(2):  # 1 次原始调用 + 1 次重试
        resp = client.complete(prompt)
        try:
            parsed = _parse(resp.content)
            break
        except (ValueError, ValidationError):
            continue

    if parsed is None:
        return _persist_degraded(db, code, assessed_at, model_version, fp)

    return _persist(
        db, code, assessed_at, model_version, fp, parsed.model_dump()
    )


def _parse(content: str):
    """从 LLM 文本中提取并强校验 JSON（容忍 ```json 围栏）。"""
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in LLM output")
    data = json.loads(text[start:end + 1])

    from .schemas import ThreeJudgmentOutput

    return ThreeJudgmentOutput.model_validate(data)


def _persist(
    db: Database, code: str, assessed_at: str, model_version: str,
    fp: str, payload: dict,
) -> AssessmentRecord:
    from datetime import date as _date

    valid_until = payload["timeliness"]["valid_until"]
    try:
        _date.fromisoformat(valid_until)
    except ValueError as exc:
        # 日期合法格式已由 pattern 保证，此处防未来语义错误
        raise ValueError(f"invalid valid_until: {valid_until}") from exc
    with db.session() as s:
        s.add(AiAssessment(
            code=code,
            assessed_at=assessed_at,
            authenticity=payload["authenticity"]["verdict"],
            authenticity_confidence=payload["authenticity"]["confidence"],
            timeliness_valid_until=valid_until,
            sustainability_score=float(payload["sustainability"]["score"]),
            sustainability_trend=payload["sustainability"]["trend"],
            model_version=model_version,
            data_fingerprint=fp,
            payload_json=json.dumps(payload, ensure_ascii=False),
            review_round=0,
        ))
        s.commit()
    return AssessmentRecord(code=code, degraded=False, fingerprint=fp)


def _persist_degraded(
    db: Database, code: str, assessed_at: str, model_version: str, fp: str,
) -> AssessmentRecord:
    """降级「未评估」：pending、其余结论空、AI 权重视为 0，不阻塞流程。"""
    with db.session() as s:
        s.add(AiAssessment(
            code=code,
            assessed_at=assessed_at,
            authenticity="pending",
            authenticity_confidence=None,
            timeliness_valid_until=None,
            sustainability_score=None,
            sustainability_trend=None,
            model_version=model_version,
            data_fingerprint=fp,
            payload_json=json.dumps({"status": "not_assessed"}, ensure_ascii=False),
            review_round=0,
        ))
        s.commit()
    return AssessmentRecord(code=code, degraded=True, fingerprint=fp)


def get_ai_factor(db: Database, code: str) -> dict:
    """规则引擎把 AI 结论当普通因子读取（spec §4.1）。

    未评估/降级时 status=not_assessed， sustainability_score=0（权重视为 0），
    authenticity=pending——不阻塞规则引擎运行。
    """
    with db.session() as s:
        row = (
            s.query(AiAssessment)
            .filter(AiAssessment.code == code)
            .order_by(AiAssessment.assessed_at.desc(), AiAssessment.id.desc())
            .first()
        )
    if row is None:
        return {
            "status": "not_assessed",
            "authenticity": "pending",
            "authenticity_confidence": None,
            "valid_until": None,
            "sustainability_score": 0.0,
            "sustainability_trend": None,
            "model_version": None,
            "data_fingerprint": None,
        }
    degraded = row.authenticity == "pending"
    return {
        "status": "ok" if not degraded else "not_assessed",
        "authenticity": row.authenticity,
        "authenticity_confidence": row.authenticity_confidence,
        "valid_until": row.timeliness_valid_until,
        "sustainability_score": row.sustainability_score or 0.0,
        "sustainability_trend": row.sustainability_trend,
        "model_version": row.model_version,
        "data_fingerprint": row.data_fingerprint,
    }
