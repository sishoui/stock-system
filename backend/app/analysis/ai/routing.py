"""分层路由与全池编排（spec §4.4，issue #19）。

流程：预算检查 → 指纹防重评 → 便宜模型批量评估（指数退避重试）→
按复核标准（suspect / 置信度 < 0.7 / 当日拟操作）升级 reasoner 复核，
复核覆盖原结论并注明轮次。连续 N 个调度窗口失败 → ERROR 告警。
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable

from pydantic import ValidationError

from .client import LLMClient, LLMResponse
from .cost import CostTracker
from .inputs import AnnouncementProvider
from .prompts import build_prompt, build_inputs
from .service import (  # noqa: F401 兼容旧私有名引用
    parse_llm_output as _parse,
    persist_assessment as _persist,
    persist_degraded_assessment as _persist_degraded,
    fingerprint,
)
from .schemas import ThreeJudgmentOutput  # noqa: F401 类型引用
from app.common.config import AIConfig
from app.data.db import Database
from app.data.db_models import AiAssessment

logger = logging.getLogger("app.analysis.ai")


@dataclass
class PoolResult:
    """一轮全池评估的结果清单。"""

    assessed: list[str] = field(default_factory=list)
    reviewed: list[str] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)
    skipped_fingerprint: list[str] = field(default_factory=list)
    not_assessed_budget: list[str] = field(default_factory=list)


def _latest_fingerprint(db: Database, code: str) -> str | None:
    """最近一次「成功评估」的指纹；降级/预算跳过行不算，下一窗口可重试。"""
    with db.session() as s:
        row = (
            s.query(AiAssessment)
            .filter(AiAssessment.code == code, AiAssessment.authenticity != "pending")
            .order_by(AiAssessment.assessed_at.desc(), AiAssessment.id.desc())
            .first()
        )
    return row.data_fingerprint if row else None


def _attempt_with_backoff(
    attempt: Callable[[], LLMResponse],
    backoff_seconds: list[int],
    sleeper: Callable[[float], None],
) -> LLMResponse:
    """1 次原始尝试 + 按退避序列重试（调用异常或输出不合规均重试）；全失败抛 LastCallError。"""

    class LastCallError(RuntimeError):
        pass

    attempts = 1 + len(backoff_seconds)
    last: Exception | None = None
    for i in range(attempts):
        try:
            return attempt()
        except Exception as exc:  # noqa: BLE001 调用异常/解析失败全部重试
            last = exc
            if i < attempts - 1:
                sleeper(backoff_seconds[i])
    raise LastCallError(str(last))


def _needs_review(payload: dict, config: AIConfig, must_review: bool) -> bool:
    auth = payload["authenticity"]
    if auth["verdict"] == "suspect" and config.review_suspect:
        return True
    if auth["confidence"] < config.review_confidence_threshold:
        return True
    return must_review and config.force_review_on_buy


def _log_cost(tracker: CostTracker, date: str, code: str, resp: LLMResponse) -> None:
    tracker.record(
        date, code, resp.model,
        tokens_in=resp.tokens_in, tokens_out=resp.tokens_out,
        cost_cny=resp.cost_cny,
    )


def _parse_and_log(
    log_fn: Callable[[LLMResponse], None],
    client: LLMClient, prompt: str, model: str,
) -> "ThreeJudgmentOutput":
    """一次调用：记成本 + 解析校验；任一失败抛异常走退避重试。"""
    resp = client.complete(prompt, model)
    log_fn(resp)
    return _parse(resp.content)


class FailureWindowTracker:
    """连续 N 个调度窗口全失败 → 日志 ERROR 告警。"""

    def __init__(self, config: AIConfig) -> None:
        self._threshold = config.consecutive_failure_alert
        self._consecutive = 0
        self._alerted = False

    def record_window(self, *, ok: bool) -> None:
        if ok:
            self._consecutive = 0
            self._alerted = False
            return
        self._consecutive += 1
        if self._consecutive >= self._threshold and not self._alerted:
            logger.error(
                "AI 连续 %d 个调度窗口失败，告警：AI 评估暂停，转降级日程（前台横幅语义）",
                self._consecutive,
            )
            self._alerted = True


def run_pool(
    db: Database,
    codes: Iterable[str],
    client: LLMClient,
    provider: AnnouncementProvider,
    config: AIConfig,
    *,
    cost_tracker: CostTracker | None = None,
    window_tracker: FailureWindowTracker | None = None,
    must_review: set[str] | frozenset[str] = frozenset(),
    sleeper: Callable[[float], None] = time.sleep,
) -> PoolResult:
    """全池批量评估（每日数据治理完成后触发）。

    must_review：当日拟操作（买入/加仓）标的，强制 reasoner 复核。
    """
    tracker = cost_tracker or CostTracker(db, config)
    windows = window_tracker or FailureWindowTracker(config)
    result = PoolResult()
    today = provider.trading_day()
    window_had_call = False
    window_ok = True

    for code in codes:
        if tracker.budget_exhausted(today):
            # 超限：当日剩余标的标「未评估」，不再调用
            _persist_budget_skipped(db, code, today, config.cheap_model)
            result.not_assessed_budget.append(code)
            continue

        inputs = build_inputs(db, code, provider)
        prompt = build_prompt(inputs)
        fp = fingerprint(inputs, config.cheap_model)
        if fp == _latest_fingerprint(db, code):
            result.skipped_fingerprint.append(code)  # 指纹不变不重评
            continue

        try:
            payload = _attempt_with_backoff(
                lambda: _parse_and_log(
                    lambda r: _log_cost(tracker, today, code, r),
                    client, prompt, config.cheap_model,
                ),
                config.backoff_seconds, sleeper,
            ).model_dump()
        except Exception:
            result.degraded.append(code)
            _persist_degraded(db, code, today, config.cheap_model, fp)
            window_ok = False
            continue

        window_had_call = True
        record = _persist(db, code, today, config.cheap_model, fp, payload)
        result.assessed.append(code)

        if _needs_review(payload, config, code in must_review):
            review_prompt = prompt + "\n\n【复核请求】请以更严格的推理重新审视上述三判断。"
            try:
                rparsed = _attempt_with_backoff(
                    lambda: _parse_and_log(
                        lambda r: _log_cost(tracker, today, code, r),
                        client, review_prompt, config.reasoner_model,
                    ),
                    config.backoff_seconds, sleeper,
                )
            except Exception:
                logger.error("AI 复核调用失败：%s（保留批量结论）", code)
                continue
            _persist_review(db, code, today, rparsed.model_dump(), fp,
                            config.reasoner_model)
            result.reviewed.append(code)

    windows.record_window(ok=window_ok)
    return result


def _log_cost(tracker: CostTracker, date: str, code: str, resp: LLMResponse) -> None:
    tracker.record(
        date, code, resp.model,
        tokens_in=resp.tokens_in, tokens_out=resp.tokens_out,
        cost_cny=resp.cost_cny,
    )


def _persist_budget_skipped(
    db: Database, code: str, assessed_at: str, model_version: str,
) -> None:
    """预算超限：标「未评估」，AI 权重视为 0，不阻塞流程。"""
    with db.session() as s:
        s.add(AiAssessment(
            code=code, assessed_at=assessed_at,
            authenticity="pending",
            model_version=model_version,
            data_fingerprint=f"budget:{code}:{assessed_at}",
            payload_json=json.dumps(
                {"status": "not_assessed", "reason": "budget_exhausted"},
                ensure_ascii=False,
            ),
            review_round=0,
        ))
        s.commit()


def _persist_review(
    db: Database, code: str, assessed_at: str, payload: dict,
    fp: str, model_version: str,
) -> None:
    """复核结论覆盖原结论，review_round 注明轮次。"""
    from datetime import date as _date

    valid_until = payload["timeliness"]["valid_until"]
    _date.fromisoformat(valid_until)
    prev_round = _latest_review_round(db, code)
    payload["review_round"] = prev_round + 1
    payload["review_model"] = model_version
    with db.session() as s:
        s.add(AiAssessment(
            code=code, assessed_at=assessed_at,
            authenticity=payload["authenticity"]["verdict"],
            authenticity_confidence=payload["authenticity"]["confidence"],
            timeliness_valid_until=valid_until,
            sustainability_score=float(payload["sustainability"]["score"]),
            sustainability_trend=payload["sustainability"]["trend"],
            model_version=model_version,
            data_fingerprint=fp,
            payload_json=json.dumps(payload, ensure_ascii=False),
            review_round=prev_round + 1,
        ))
        s.commit()


def _latest_review_round(db: Database, code: str) -> int:
    with db.session() as s:
        row = (
            s.query(AiAssessment)
            .filter(AiAssessment.code == code)
            .order_by(AiAssessment.assessed_at.desc(), AiAssessment.id.desc())
            .first()
        )
    return row.review_round if row else 0
