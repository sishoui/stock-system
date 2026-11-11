"""硬门槛 H1–H5（spec §5.1）。触发即剔除，返回规则编号列表。"""
from __future__ import annotations

# 金融、地产行业 H4 豁免（高杠杆属行业常态，行业自适应阈值）
H4_EXEMPT_INDUSTRIES = frozenset({"银行", "非银金融", "保险", "证券", "房地产"})

ROE_TTM_MIN = 8.0
ROE_ANNUAL_MIN = 5.0
DEBT_RATIO_MAX = 70.0
CASHFLOW_PROFIT_RATIO_MIN = 0.5


def check_hard_gates(
    *,
    code: str,
    is_st: bool,
    list_years: float,
    roe_ttm: float | None,
    roe_3y_min: float | None,
    debt_ratio: float | None,
    industry: str | None,
    operating_cashflow: float | None,
    net_profit: float | None,
) -> list[str]:
    """执行 H1–H5，返回触发的规则编号（按 H1..H5 顺序）。空列表 = 通过。"""
    reasons: list[str] = []

    # H1 非 ST、非 *ST
    if is_st:
        reasons.append("H1")

    # H2 上市满 2 年
    if list_years < 2.0:
        reasons.append("H2")

    # H3 最近一季 ROE(TTM) ≥ 8%，且最近 3 年年报 ROE 均 ≥ 5%
    if roe_ttm is None or roe_ttm < ROE_TTM_MIN:
        reasons.append("H3")
    elif roe_3y_min is None or roe_3y_min < ROE_ANNUAL_MIN:
        reasons.append("H3")

    # H4 资产负债率 < 70%（金融/地产豁免）
    if debt_ratio is not None and debt_ratio >= DEBT_RATIO_MAX:
        if industry not in H4_EXEMPT_INDUSTRIES:
            reasons.append("H4")

    # H5 最近一年经营现金流 > 0，且 现金流/净利润 > 0.5
    if operating_cashflow is None or operating_cashflow <= 0:
        reasons.append("H5")
    elif net_profit is not None and net_profit > 0:
        if operating_cashflow / net_profit <= CASHFLOW_PROFIT_RATIO_MIN:
            reasons.append("H5")
    # 净利润为负或缺失：仅要求现金流>0，比例无法判定不剔除

    return reasons
