"""三判断输出契约（spec §4.3，Pydantic v2 强校验）。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Verdict = Literal["pass", "suspect", "fail"]
DecayHint = Literal["fast", "medium", "slow"]
Trend = Literal["improving", "stable", "deteriorating"]


class AuthenticityJudgment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Verdict
    confidence: float = Field(ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)


class TimelinessJudgment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid_until: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    decay_hint: DecayHint
    reasons: list[str] = Field(default_factory=list)


class SustainabilityJudgment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: int = Field(ge=0, le=10)
    trend: Trend
    key_risks: list[str] = Field(default_factory=list)


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    quote: str


class ThreeJudgmentOutput(BaseModel):
    """一次 LLM 调用输出的三判断合一 JSON。"""

    model_config = ConfigDict(extra="forbid")

    authenticity: AuthenticityJudgment
    timeliness: TimelinessJudgment
    sustainability: SustainabilityJudgment
    evidence: list[EvidenceItem] = Field(default_factory=list)
