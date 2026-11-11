"""参数搜索一轮 + 2024–2025 留出验证报告（issue #26，spec §7.5/§8）。

离线运行（stub/合成数据即可，不联网）：

    python -m scripts.holdout_validation

输出：docs/holdout-report-2024-2025.md
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.backtest.engine import BacktestEngine
from app.backtest.sweep import run_parameter_sweep
from app.common.config import RiskConfig
from app.data.db import Database


def seed_synthetic(db: Database, days: int = 760) -> None:
    """合成 2023–2025 日线：基准 + 池内个股温和上行。"""
    from sqlalchemy import text

    from app.strategy.pool_models import PoolBase, PoolMembership

    PoolBase.metadata.create_all(db.engine)
    import random

    random.seed(42)
    codes = ["000300.SH"] + [f"{600000 + i}.SH" for i in range(5)]
    base = date(2023, 1, 2)
    with db.session() as s:
        for code in codes:
            c = 10.0
            d = base
            n = 0
            while n < days:
                d += timedelta(days=1)
                if d.weekday() >= 5:
                    continue
                drift = 0.0008 if code != "000300.SH" else 0.0003
                c *= 1 + random.gauss(drift, 0.015)
                o = c * (1 - random.uniform(0, 0.004))
                s.execute(text(
                    "INSERT INTO daily_bar(code,date,open,high,low,close,volume,amount)"
                    " VALUES(:c,:dt,:o,:h,:l,:cl,:v,:a)"
                ), {"c": code, "dt": d.isoformat(), "o": o, "h": c * 1.01,
                    "l": o * 0.99, "cl": c, "v": 900_000, "a": c * 900_000})
                n += 1
        for i, code in enumerate(codes[1:]):
            s.add(PoolMembership(code=code, entered_at=base.isoformat(),
                                 rank=i + 1, score=90.0 - i))
        s.commit()


def main() -> None:
    db = Database("sqlite:///data/holdout.db")
    db.create_all()
    seed_synthetic(db)

    grid = {
        "atr_multiplier": [1.5, 2.0, 2.5],
        "trail_pct": [0.05, 0.08],
        "max_positions": [6, 10],
    }
    # 2023 训练段参数搜索
    sweep = run_parameter_sweep(db, grid, start="2023-01-01", end="2023-12-31")
    sweep.sort(key=lambda cr: cr[1].metrics.get("total_return", -9), reverse=True)
    best_cfg, best_res = sweep[0]

    # 2024–2025 留出段验证（最优参数）
    holdout = BacktestEngine(db, best_cfg, start="2024-01-01", end="2025-12-31").run()
    bm = holdout.metrics

    lines = [
        "# 2024–2025 留出验证报告（issue #26）",
        "",
        "> 数据为离线合成行情（stub），用于验证参数搜索 → 留出验证链路；联网真实数据由数据管线（#13/#14）替换后重跑。",
        "",
        "## 参数搜索（训练段 2023，共 %d 组）" % len(sweep),
        "",
        "| atr_multiplier | trail_pct | max_positions | 训练总收益 | 训练最大回撤 |",
        "|---|---|---|---|---|",
    ]
    for cfg, res in sweep:
        m = res.metrics
        lines.append(f"| {cfg.atr_multiplier} | {cfg.trail_pct} | {cfg.max_positions} "
                     f"| {m['total_return']*100:.2f}% | {m['max_drawdown']*100:.2f}% |")
    lines += [
        "",
        f"最优参数：atr_multiplier={best_cfg.atr_multiplier}, "
        f"trail_pct={best_cfg.trail_pct}, max_positions={best_cfg.max_positions}",
        "",
        "## 留出段（2024-01-01 ~ 2025-12-31，最优参数）",
        "",
        "| 指标 | 数值 |",
        "|---|---|",
        f"| 总收益 | {bm['total_return']*100:.2f}% |",
        f"| 年化收益 | {bm['annualized_return']*100:.2f}% |",
        f"| 最大回撤 | {bm['max_drawdown']*100:.2f}% |",
        f"| 夏普 | {bm['sharpe']:.2f} |",
        f"| 胜率（按笔） | {bm['win_rate']*100:.2f}% |",
        f"| 沪深300 收益 | {bm['benchmark_total_return']*100:.2f}% |",
        f"| 超额收益 | {bm['excess_return']*100:.2f}% |",
        "",
        "## 结论",
        "",
        "- 链路验证通过：训练段参数搜索 → 最优参数在 2024–2025 留出段产出完整指标。",
        "- 基线即上表数值；真实数据接入后以同脚本重跑刷新基线。",
    ]
    out = Path(__file__).resolve().parents[2] / "docs" / "holdout-report-2024-2025.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"report written to {out}")


if __name__ == "__main__":
    main()
