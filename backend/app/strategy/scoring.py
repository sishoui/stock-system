"""三维打分与归一化（spec §5.2）。所有分项 0~100，加权合成。"""
from __future__ import annotations

from statistics import stdev

WEIGHTS = {"quality": 0.4, "growth": 0.3, "valuation": 0.3, "ai": 0.0}


def normalize_rank_0_100(values: list[float], higher_is_better: bool = True) -> list[float]:
    """按排名归一化到 0~100（min-max）：同输入同输出，稳定可复现。"""
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi == lo:
        return [50.0] * len(values)
    span = hi - lo
    out = [(v - lo) / span * 100.0 for v in values]
    if not higher_is_better:
        out = [100.0 - o for o in out]
    return out


def cross_section_percentile(value: float, peers: dict[str, float]) -> float:
    """横截面分位 0~100：value 在同行分布中的位置。"""
    vals = sorted(peers.values())
    if not vals:
        return 50.0
    return _percentile(value, vals)


def historical_percentile(value: float, history: list[float]) -> float:
    """历史分位 0~100：value 相对自身过去 5 年分布的位置。"""
    if not history:
        return 50.0
    return _percentile(value, sorted(history))


def _percentile(value: float, sorted_vals: list[float]) -> float:
    lo, hi = sorted_vals[0], sorted_vals[-1]
    if hi == lo:
        return 50.0
    # 线性插值：低于最小值=0，高于最大值=100
    if value <= lo:
        return 0.0
    if value >= hi:
        return 100.0
    rank = sum(1 for v in sorted_vals if v < value)
    frac = (value - lo) / (hi - lo)
    # 分位 = (小于它的个数 + 区间内插值) / (n-1) * 100
    return (rank - 1 + frac) / (len(sorted_vals) - 1) * 100.0 if rank > 0 else frac / (len(sorted_vals) - 1) * 100.0


def quality_score(
    roe_ttm: float,
    roe_annuals: list[float],
    gross_margins: list[float],
    peers_roe: dict[str, float],
) -> float:
    """质量分：ROE 水平（横截面）+ ROE 稳定性（3 年标准差越小越好）+ 毛利率水平。各占 1/3。"""
    components: list[float] = []

    roe_cs = cross_section_percentile(roe_ttm, peers_roe) if len(peers_roe) > 1 else 50.0
    components.append(roe_cs)

    if len(roe_annuals) >= 2:
        sd = stdev(roe_annuals)
        mean = sum(roe_annuals) / len(roe_annuals)
        # 变异系数越小越稳：映射 0~100，cv=0 → 100，cv≥0.5 → 0
        cv = abs(sd / mean) if mean != 0 else 10.0
        components.append(max(0.0, min(100.0, (1.0 - cv / 0.5) * 100.0)))
    else:
        components.append(50.0)

    if gross_margins:
        gm = gross_margins[-1]
        components.append(max(0.0, min(100.0, gm * 2.0)))  # 50% 毛利率 → 100
    else:
        components.append(50.0)

    return sum(components) / len(components)


def growth_score(
    revenue_growth: float | None,
    profit_growth: float | None,
    peers_revenue_growth: dict[str, float],
) -> float:
    """成长分：营收增速（全市场横截面标准化）+ 净利增速，各占一半。"""
    parts: list[float] = []
    if revenue_growth is not None and len(peers_revenue_growth) > 1:
        parts.append(cross_section_percentile(revenue_growth, peers_revenue_growth))
    else:
        parts.append(50.0)
    if profit_growth is not None:
        parts.append(max(0.0, min(100.0, 50.0 + profit_growth)))  # 0% → 50, +50% → 100
    else:
        parts.append(50.0)
    return sum(parts) / len(parts)


def valuation_score(
    pe: float | None,
    pb: float | None,
    pe_history: list[float],
    pb_history: list[float],
    peers_pe: dict[str, float],
    peers_pb: dict[str, float],
) -> float:
    """估值分：PE 与 PB 各半，每项 = 历史 5 年分位与行业内横截面分位各半；分位越低越便宜分越高。"""
    parts: list[float] = []

    def one(cur: float | None, hist: list[float], peers: dict[str, float]) -> float | None:
        if cur is None or cur <= 0:
            return None
        hp = historical_percentile(cur, hist) if hist else 50.0
        cp = cross_section_percentile(cur, peers) if len(peers) > 1 else 50.0
        return 100.0 - (hp + cp) / 2.0

    pe_part = one(pe, pe_history, peers_pe)
    if pe_part is not None:
        parts.append(pe_part)
    pb_part = one(pb, pb_history, peers_pb)
    if pb_part is not None:
        parts.append(pb_part)
    if not parts:
        return 50.0
    return sum(parts) / len(parts)


def combine(quality: float, growth: float, valuation: float, ai: float = 0.0,
            weights: dict[str, float] | None = None) -> float:
    """加权合成总分；AI 可持续性此时权重为 0（预留接口）。"""
    w = weights or WEIGHTS
    return quality * w["quality"] + growth * w["growth"] + valuation * w["valuation"] + ai * w["ai"]
