"""回测指标（spec §8.3 清单）：收益/回撤/夏普/胜率/盈亏比/换手/相对沪深300。"""
from __future__ import annotations

import math
from datetime import date

TRADING_DAYS = 252


def _max_drawdown(nav: list[float]) -> tuple[float, int, int]:
    """返回 (最大回撤, 峰值下标, 谷值下标)。"""
    peak = nav[0]
    peak_i = 0
    mdd, pk, tr = 0.0, 0, 0
    for i, v in enumerate(nav):
        if v > peak:
            peak, peak_i = v, i
        dd = (peak - v) / peak
        if dd > mdd:
            mdd, pk, tr = dd, peak_i, i
    return mdd, pk, tr


def compute_metrics(nav: list[float], dates: list[str], benchmark_nav: list[float],
                    trades: list[dict], traded_value: float,
                    initial_capital: float = 1.0) -> dict:
    """nav 与 benchmark_nav 等长同日期。trades 每笔含 pnl_pct（百分比，如 10.0 = +10%）。"""
    n = len(nav)
    total = nav[-1] / nav[0] - 1.0
    years = max((n - 1) / TRADING_DAYS, 1e-9)
    annualized = (1.0 + total) ** (1.0 / years) - 1.0 if total > -1 else -1.0

    mdd, pk, tr = _max_drawdown(nav)
    rets = [nav[i] / nav[i - 1] - 1.0 for i in range(1, n)]
    mean = sum(rets) / len(rets) if rets else 0.0
    std = math.sqrt(
        sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    ) if len(rets) > 1 else 0.0
    sharpe = (mean / std) * math.sqrt(TRADING_DAYS) if std > 1e-12 else 0.0

    pnls = [t["pnl_pct"] / 100.0 for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    win_rate = len(wins) / len(pnls) if pnls else 0.0
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0
    plr = avg_win / avg_loss if avg_loss > 1e-12 else 0.0
    max_single_loss = min(pnls) if pnls else 0.0

    def _years_span() -> float:
        if len(dates) < 2:
            return years
        d0, d1 = date.fromisoformat(str(dates[0])), date.fromisoformat(str(dates[-1]))
        return max((d1 - d0).days / 365.25, 1e-9)

    turnover = traded_value / max(initial_capital, 1e-9) / _years_span()

    bm_total = benchmark_nav[-1] / benchmark_nav[0] - 1.0
    bm_mdd = _max_drawdown(benchmark_nav)[0]
    excess = total - bm_total
    rel_dd = mdd - bm_mdd

    return {
        "total_return": total,
        "annualized_return": annualized,
        "max_drawdown": mdd,
        "dd_peak_date": dates[pk],
        "dd_trough_date": dates[tr],
        "sharpe": sharpe,
        "win_rate": win_rate,
        "profit_loss_ratio": plr,
        "max_single_loss": max_single_loss,
        "turnover_annualized": turnover,
        "benchmark_total_return": bm_total,
        "excess_return": excess,
        "relative_drawdown": rel_dd,
    }
