"""股票池动态维护（issue #21，spec §5.3）。

- 全量重算：财报季（4/8/10 月）对全市场重跑初筛打分，重建全池（N=80 可配置）。
- 增量剔除：月度/事件驱动——新增 ST、退市风险警示、财报异常（暴雷）、AI 真伪 fail 即时移出并留痕。
- 换仓：排名跌入后 30% 或触发硬门槛跌出；新财报后打分前 N 进入。
- 池内排序：含 AI 可持续性加权信号；AI 缺失时权重为 0 不阻塞（#18/#19 读取接口）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.analysis.ai.service import get_ai_factor
from app.data.db import Database
from app.data.db_models import AiAssessment, FinanceIndicator, StockProfile, Valuation
from app.strategy.engine import RankedStock, run_screening
from app.strategy.pool_models import PoolBase, PoolChangeLog, PoolMembership

POOL_SIZE_DEFAULT = 80
BOTTOM_DROP_FRACTION = 0.30  # 排名跌入后 30% 跌出
AI_WEIGHT_DEFAULT = 0.3      # AI 可持续性在池内排序中的权重
# 暴雷阈值（财报异常即时剔除）
CRASH_ROE_MAX = 0.0
CRASH_DEBT_RATIO_MIN = 85.0
CRASH_PROFIT_GROWTH_MIN = -100.0


@dataclass
class RebuildResult:
    as_of: str
    ranked: list[RankedStock]
    entered: list[str]
    removed: list[str] = field(default_factory=list)


def _ensure_tables(db: Database) -> None:
    PoolBase.metadata.create_all(db.engine)


def rebuild_pool(db: Database, as_of: str, n: int = POOL_SIZE_DEFAULT,
                 ai_weight: float = AI_WEIGHT_DEFAULT) -> RebuildResult:
    """全量重算重建全池：清空成员表，硬门槛通过者打分取前 N 入池，留痕。

    换仓留痕：跌入后 30% 或触发硬门槛的旧成员按 remove 记录。
    """
    _ensure_tables(db)
    result = run_screening(db, as_of)

    with db.session() as s:
        old_codes = [m.code for m in s.query(PoolMembership).all()]
    suspicious = {e.code for e in result.excluded}
    removed = [c for c in old_codes if c in suspicious or _in_bottom(c, result.ranked)]

    top = [r.code for r in result.ranked[:n]]
    ai_scores = _ai_sustainability_scores(db, top)

    with db.session() as s:
        s.query(PoolMembership).delete()
        for i, code in enumerate(top):
            s.add(PoolMembership(
                code=code, entered_at=as_of, rank=i + 1,
                score=result.ranked[i].score,
                ai_sustainability=ai_scores.get(code),
                reason="full_rebuild",
            ))
        for code in top:
            if code not in set(old_codes):
                s.add(PoolChangeLog(date=as_of, code=code, action="enter",
                                    reason="full_rebuild"))
        for code in removed:
            s.add(PoolChangeLog(date=as_of, code=code, action="remove",
                                reason="rank_drop_or_gate"))
        s.commit()
    return RebuildResult(as_of=as_of, ranked=result.ranked, entered=top, removed=removed)


def run_incremental_removal(db: Database, as_of: str) -> list[str]:
    """月度/事件驱动剔除：返回当日移出的代码。

    规则：新增 ST、退市风险警示、财报异常（暴雷：ROE≤0 且负债率≥85% 且净利增速≤-100%
    或现金流为负）、AI 真伪 fail —— 即时移出并留痕。
    """
    _ensure_tables(db)
    removed: list[str] = []
    with db.session() as s:
        members = [m.code for m in s.query(PoolMembership).all()]

    for code in members:
        reason = _removal_reason(db, code, as_of)
        if reason:
            _remove(db, as_of, code, reason)
            removed.append(code)
    return removed


def rank_pool(db: Database, as_of: str, ai_weight: float = AI_WEIGHT_DEFAULT) -> list[RankedStock]:
    """池内排序：基础三维分 + AI 可持续性加权信号。

    AI 缺失（未评估/降级）时该票 ai 分取 0、有效权重为 0，不阻塞排序。
    """
    _ensure_tables(db)
    with db.session() as s:
        members = {m.code: m.score for m in s.query(PoolMembership).all()}
    ai_scores = _ai_sustainability_scores(db, list(members))

    ranked: list[RankedStock] = []
    for code, base in members.items():
        sus = ai_scores.get(code)
        # AI 缺失 → sus 为 None，信号为 0 且不占权重（缺失者按纯三维分排序）
        ai_signal = sus if sus is not None else 0.0
        w = ai_weight if sus is not None else 0.0
        total = base * (1.0 - w) + ai_signal * w
        ranked.append(RankedStock(code=code, score=total, quality=base,
                                  growth=0.0, valuation=0.0,
                                  reasons=[f"AI可持续性{sus:.0f}" if sus is not None else "AI未评估"]))
    ranked.sort(key=lambda r: (-r.score, r.code))
    return ranked


# -- 内部 -------------------------------------------------------------------

def _in_bottom(code: str, ranked: list[RankedStock]) -> bool:
    if not ranked:
        return False
    bottom = max(1, int(len(ranked) * BOTTOM_DROP_FRACTION))
    tail = {r.code for r in ranked[-bottom:]}
    return code in tail


def _ai_sustainability_scores(db: Database, codes: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for code in codes:
        factor = get_ai_factor(db, code)
        # 仅 authenticity=pass 的可持续分计入；suspect/fail/pending 缺失权重为 0
        if factor["status"] == "ok" and factor["authenticity"] == "pass" \
                and factor["sustainability_score"] is not None:
            out[code] = float(factor["sustainability_score"])
    return out


def _removal_reason(db: Database, code: str, as_of: str) -> str | None:
    with db.session() as s:
        p = s.query(StockProfile).filter(StockProfile.code == code).first()
        if p is None:
            return None
        # 新增 ST / 退市风险警示
        if p.is_st:
            return "new_st"
        if p.delist_date or "退市风险" in (p.status_history or ""):
            return "delisting_risk"
        latest = (
            s.query(FinanceIndicator)
            .filter(FinanceIndicator.code == code,
                    FinanceIndicator.report_period <= as_of)
            .order_by(FinanceIndicator.report_period.desc())
            .first()
        )
    # 财报异常 / 暴雷
    if latest is not None:
        roe = latest.roe_ttm if latest.roe_ttm is not None else latest.roe
        if (roe is not None and roe <= CRASH_ROE_MAX
                and latest.debt_ratio is not None
                and latest.debt_ratio >= CRASH_DEBT_RATIO_MIN
                and latest.profit_growth is not None
                and latest.profit_growth <= CRASH_PROFIT_GROWTH_MIN):
            return "financial_crash"
        if latest.operating_cashflow is not None and latest.operating_cashflow < 0:
            return "financial_anomaly"
    # AI 真伪 fail
    factor = get_ai_factor(db, code)
    if factor["status"] == "ok" and factor["authenticity"] == "fail":
        return "ai_authenticity_fail"
    return None


def _remove(db: Database, as_of: str, code: str, reason: str) -> None:
    with db.session() as s:
        s.query(PoolMembership).filter(PoolMembership.code == code).delete()
        s.add(PoolChangeLog(date=as_of, code=code, action="remove", reason=reason))
        s.commit()
