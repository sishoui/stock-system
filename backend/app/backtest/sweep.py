"""RiskConfig 参数遍历（spec §7.5/§8.2）：同一配置对象批量跑多组参数。"""
from __future__ import annotations

import itertools

from app.backtest.engine import BacktestEngine
from app.common.config import RiskConfig


def _grid_configs(grid: dict) -> list[RiskConfig]:
    keys = list(grid)
    out = []
    for values in itertools.product(*(grid[k] for k in keys)):
        out.append(RiskConfig(**dict(zip(keys, values))))
    return out


def run_parameter_sweep(db, grid: dict, initial_capital: float = 200_000.0,
                        start: str = "", end: str = "",
                        benchmark_code: str = "000300.SH"):
    """按网格遍历 RiskConfig 参数，返回 [(config, BacktestResult), ...]。"""
    results = []
    for cfg in _grid_configs(grid):
        eng = BacktestEngine(db, cfg, initial_capital=initial_capital,
                             start=start, end=end, benchmark_code=benchmark_code)
        results.append((cfg, eng.run()))
    return results
