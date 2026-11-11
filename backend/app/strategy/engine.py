"""选股引擎（spec §5）：读 #16 表结构 → 硬门槛初筛 → 三维打分 → 稳定排序名单。

只读 data 层（app.data.db / db_models），不修改数据层文件。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.data.db import Database
from app.data.db_models import FinanceIndicator, StockProfile, Valuation
from app.strategy import scoring
from app.strategy.filters import check_hard_gates


@dataclass
class RankedStock:
    code: str
    score: float
    quality: float
    growth: float
    valuation: float
    reasons: list[str] = field(default_factory=list)


@dataclass
class ExcludedStock:
    code: str
    reasons: list[str]


@dataclass
class ScreeningResult:
    ranked: list[RankedStock]
    excluded: list[ExcludedStock]
    weights: dict[str, float]


def run_screening(db: Database, as_of: str) -> ScreeningResult:
    """对全市场执行初筛与打分。同一数据同一结果（稳定排序：总分降序，同分按代码升序）。"""
    with db.session() as s:
        profiles = {p.code: p for p in s.query(StockProfile).all()}
        fin_rows = (
            s.query(FinanceIndicator)
            .filter(FinanceIndicator.report_period <= as_of)
            .order_by(FinanceIndicator.code, FinanceIndicator.report_period)
            .all()
        )
        val_rows = (
            s.query(Valuation)
            .filter(Valuation.date <= as_of)
            .order_by(Valuation.code, Valuation.date)
            .all()
        )

    fin_by_code: dict[str, list[FinanceIndicator]] = {}
    for f in fin_rows:
        fin_by_code.setdefault(f.code, []).append(f)
    val_by_code: dict[str, list[Valuation]] = {}
    for v in val_rows:
        val_by_code.setdefault(v.code, []).append(v)

    def latest_annuals(code: str) -> list[FinanceIndicator]:
        return [f for f in fin_by_code.get(code, []) if f.report_period.endswith("-12-31")]

    # 先逐票做硬门槛判定
    excluded: list[ExcludedStock] = []
    passed: list[str] = []
    gate_info: dict[str, dict] = {}
    for code, p in sorted(profiles.items()):
        fins = fin_by_code.get(code, [])
        annuals = latest_annuals(code)
        latest = fins[-1] if fins else None
        roe_ttm = latest.roe_ttm if latest else None
        if roe_ttm is None and latest is not None:
            roe_ttm = latest.roe
        roe_3y_min = (
            min(a.roe for a in annuals[-3:] if a.roe is not None)
            if any(a.roe is not None for a in annuals[-3:])
            else None
        )
        list_years = _years_between(p.list_date, as_of)
        reasons = check_hard_gates(
            code=code,
            is_st=p.is_st,
            list_years=list_years,
            roe_ttm=roe_ttm,
            roe_3y_min=roe_3y_min,
            debt_ratio=latest.debt_ratio if latest else None,
            industry=None,  # 行业字段 #16 表未含，豁免判定由行业数据接入后启用
            operating_cashflow=latest.operating_cashflow if latest else None,
            net_profit=latest.net_profit if latest else None,
        )
        if reasons:
            excluded.append(ExcludedStock(code=code, reasons=reasons))
        else:
            passed.append(code)
            gate_info[code] = {"latest": latest, "annuals": annuals}

    # 横截面 peer 集（仅通过者，保持排序稳定）
    peers_roe = {
        c: (g["latest"].roe_ttm or g["latest"].roe or 0.0)
        for c, g in gate_info.items()
        if (g["latest"].roe_ttm or g["latest"].roe) is not None
    }
    peers_rg = {
        c: g["latest"].revenue_growth
        for c, g in gate_info.items()
        if g["latest"].revenue_growth is not None
    }
    peers_pe = {c: _latest_val(val_by_code, c).pe for c in passed if (v := _latest_val(val_by_code, c)) and v.pe is not None}
    peers_pb = {c: _latest_val(val_by_code, c).pb for c in passed if (v := _latest_val(val_by_code, c)) and v.pb is not None}

    ranked: list[RankedStock] = []
    for code in passed:
        g = gate_info[code]
        latest, annuals = g["latest"], g["annuals"]
        hist = val_by_code.get(code, [])
        pe_hist = [v.pe for v in hist if v.pe is not None]
        pb_hist = [v.pb for v in hist if v.pb is not None]
        roe_annuals = [a.roe for a in annuals if a.roe is not None]
        gross_margins = [f.gross_margin for f in fin_by_code.get(code, []) if f.gross_margin is not None]

        q = scoring.quality_score(
            roe_ttm=latest.roe_ttm or latest.roe or 0.0,
            roe_annuals=roe_annuals,
            gross_margins=gross_margins,
            peers_roe=peers_roe,
        )
        gr = scoring.growth_score(latest.revenue_growth, latest.profit_growth, peers_rg)
        va = scoring.valuation_score(
            pe=_latest_val(val_by_code, code).pe if hist else None,
            pb=_latest_val(val_by_code, code).pb if hist else None,
            pe_history=pe_hist[-20:],
            pb_history=pb_hist[-20:],
            peers_pe=peers_pe,
            peers_pb=peers_pb,
        )
        total = scoring.combine(q, gr, va)
        ranked.append(RankedStock(
            code=code, score=total, quality=q, growth=gr, valuation=va,
            reasons=_explain(code, q, gr, va, latest),
        ))

    ranked.sort(key=lambda r: (-r.score, r.code))
    excluded.sort(key=lambda e: e.code)
    return ScreeningResult(ranked=ranked, excluded=excluded, weights=dict(scoring.WEIGHTS))


def _latest_val(val_by_code: dict[str, list[Valuation]], code: str) -> Valuation | None:
    vs = val_by_code.get(code)
    return vs[-1] if vs else None


def _years_between(start: str, end: str) -> float:
    from datetime import date

    def parse(s: str) -> date:
        parts = s.split("-")
        return date(int(parts[0]), int(parts[1]), int(parts[2]))

    return (parse(end) - parse(start)).days / 365.25


def _explain(code: str, q: float, gr: float, va: float, latest: FinanceIndicator) -> list[str]:
    reasons = [
        f"质量分{q:.1f}（ROE TTM {latest.roe_ttm or latest.roe or 0:.1f}%）",
        f"成长分{gr:.1f}（营收增速 {latest.revenue_growth if latest.revenue_growth is not None else 'NA'}%）",
        f"估值分{va:.1f}",
    ]
    return reasons
