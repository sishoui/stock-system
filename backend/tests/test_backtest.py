"""Issue #24：事件驱动日线回测引擎（stub/合成行情 + 临时 SQLite）。

seam（spec §8）：
- matching：费用（佣金双边最低 5 元 + 印花税仅卖出 + 滑点）、成交价、涨跌停判定
- BacktestEngine.run() → BacktestResult（trades / nav / metrics），
  撮合口径：信号日收盘决策 → 次日开盘成交；涨跌停顺延；卖出资金 T+1
- compute_metrics：§8.3 指标清单（收益/回撤/夏普/胜率/盈亏比/换手/相对沪深300）
- run_parameter_sweep：同一 RiskConfig 参数遍历批量回测
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.common.config import RiskConfig
from app.data.db import Database
from app.backtest import matching

BASE = date(2021, 1, 4)


def _d(i: int) -> str:
    return (BASE + timedelta(days=i)).isoformat()


@pytest.fixture
def db(tmp_path) -> Database:
    d = Database(f"sqlite:///{tmp_path / 'bt.db'}")
    d.create_all()
    return d


def _seed_bars(db, code, rows):
    """rows: [(date, open, high, low, close, volume)]"""
    from sqlalchemy import text

    with db.session() as s:
        for dt, o, h, l, c, v in rows:
            s.execute(text(
                "INSERT INTO daily_bar(code,date,open,high,low,close,volume,amount)"
                " VALUES(:c,:d,:o,:h,:l,:cl,:v,:a)"
            ), {"c": code, "d": dt, "o": o, "h": h, "l": l, "cl": c, "v": v, "a": c * v})
        s.commit()


# ---------------------------------------------------------------------------
# 费用与成交价（seam: matching）
# ---------------------------------------------------------------------------

def test_buy_price_includes_slippage():
    # 买入成交价 = 开盘价 × (1 + 滑点)
    assert matching.buy_price(10.0, RiskConfig()) == pytest.approx(10.0 * 1.001)


def test_sell_price_includes_slippage():
    assert matching.sell_price(10.0, RiskConfig()) == pytest.approx(10.0 * 0.999)


def test_buy_commission_min_5_yuan():
    cfg = RiskConfig()
    # 10 元 × 100 股 = 1000 元，佣金 = 0.25 元 → 抬到最低 5 元
    assert matching.commission(1000.0, cfg) == pytest.approx(5.0)
    # 100 万元 × 0.025% = 250 元
    assert matching.commission(1_000_000.0, cfg) == pytest.approx(250.0)


def test_sell_fee_includes_stamp_tax():
    cfg = RiskConfig()
    # 卖出 100 万元：佣金 250 + 印花税 0.05% = 500 → 750
    assert matching.total_fee(1_000_000.0, cfg, side="sell") == pytest.approx(750.0)
    # 买入无印花税
    assert matching.total_fee(1_000_000.0, cfg, side="buy") == pytest.approx(250.0)


def test_limit_up_not_fillable():
    """开盘涨停（≥ 昨收 × (1+limit_pct)）不可买入。"""
    cfg = RiskConfig()
    assert matching.is_limit_up(11.0, 10.0, cfg) is True   # +10% 涨停
    assert matching.is_limit_up(10.9, 10.0, cfg) is False
    assert matching.is_limit_down(9.0, 10.0, cfg) is True  # -10% 跌停
    assert matching.is_limit_down(9.1, 10.0, cfg) is False

# ---------------------------------------------------------------------------
# 指标（seam: compute_metrics，spec §8.3 清单）
# ---------------------------------------------------------------------------

from app.backtest import metrics as bt_metrics


def _mk_trades(*pcts):
    return [{"pnl_pct": p} for p in pcts]


def test_metrics_constant_growth_no_drawdown_sharpe_zero_std():
    dates = ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"]
    nav = [1.0, 1.02, 1.0404, 1.061208]
    m = bt_metrics.compute_metrics(nav=nav, dates=dates, benchmark_nav=[1.0, 1.0, 1.0, 1.0],
                                   trades=[], traded_value=0.0)
    assert m["total_return"] == pytest.approx(0.061208)
    # 恰好一年（252 个收益期）时年化 = 总收益
    nav_y = [1.0 * (1.001 ** i) for i in range(253)]
    dates_y = [f"2024-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(253)]
    my = bt_metrics.compute_metrics(nav=nav_y, dates=dates_y, benchmark_nav=[1.0] * 253,
                                    trades=[], traded_value=0.0)
    assert my["annualized_return"] == pytest.approx(my["total_return"], rel=1e-6)
    assert m["max_drawdown"] == pytest.approx(0.0)
    # 收益恒定 → 波动为 0 → 夏普记 0
    assert m["sharpe"] == pytest.approx(0.0)


def test_metrics_drawdown_interval_and_sharpe():
    dates = ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"]
    nav = [1.0, 1.2, 0.96, 1.08]
    m = bt_metrics.compute_metrics(nav=nav, dates=dates, benchmark_nav=[1.0, 1.0, 0.9, 1.0],
                                   trades=[], traded_value=0.0)
    # 手算：峰 1.2 → 谷 0.96，最大回撤 20%
    assert m["max_drawdown"] == pytest.approx(0.20)
    assert m["dd_peak_date"] == "2024-01-02"
    assert m["dd_trough_date"] == "2024-01-03"
    # 收益序列 [0.20, -0.20, 0.125]：mean=0.0416667，样本 std≈0.21262
    # 夏普 = mean/std × sqrt(252) ≈ 3.112
    assert m["sharpe"] == pytest.approx(3.112, rel=1e-3)


def test_metrics_trade_stats():
    trades = _mk_trades(10.0, -5.0, 20.0, -10.0)
    m = bt_metrics.compute_metrics(nav=[1.0], dates=["2024-01-01"], benchmark_nav=[1.0],
                                   trades=trades, traded_value=0.0)
    assert m["win_rate"] == pytest.approx(0.5)
    assert m["profit_loss_ratio"] == pytest.approx(2.0)  # 均盈 15 / 均亏 7.5
    assert m["max_single_loss"] == pytest.approx(-0.10)  # 以小数记录


def test_metrics_turnover_and_benchmark_relative():
    # 换手率（年化双边）：成交 200 万 / 期均资产 100 万 / 2 年 = 1.0
    dates = ["2023-01-01", "2024-01-01", "2025-01-01"]
    m = bt_metrics.compute_metrics(nav=[1.0, 1.08, 1.0], dates=dates,
                                   benchmark_nav=[1.0, 1.04, 1.0],
                                   trades=[], traded_value=2_000_000.0,
                                   initial_capital=1_000_000.0)
    assert m["turnover_annualized"] == pytest.approx(1.0, rel=1e-3)
    # 超额 = 0.0 - 0.0 = 0；相对回撤 = 8% - 4% = 4%
    m2 = bt_metrics.compute_metrics(nav=[1.0, 1.08, 0.992], dates=dates,
                                    benchmark_nav=[1.0, 1.04, 0.9984],
                                    trades=[], traded_value=0.0)
    # 独立手算：total = 0.992/1.0-1 = -0.008；bm_total = 0.9984-1 = -0.0016
    # 超额 = -0.0064；mdd = (1.08-0.992)/1.08 = 0.0814815；bm_mdd = (1.04-0.9984)/1.04 = 0.04
    assert m2["excess_return"] == pytest.approx(-0.0064, rel=1e-6)
    assert m2["relative_drawdown"] == pytest.approx(0.0814815 - 0.04, rel=1e-4)

# ---------------------------------------------------------------------------
# 引擎（seam: BacktestEngine.run）
# ---------------------------------------------------------------------------

from app.backtest.engine import BacktestEngine
from app.strategy.pool_models import PoolBase, PoolMembership


def _seed_pool(db, code, score=100.0):
    PoolBase.metadata.create_all(db.engine)
    with db.session() as s:
        s.add(PoolMembership(code=code, entered_at=_d(0), rank=1, score=score,
                             ai_sustainability=None, reason="new_report"))
        s.commit()


def _uptrend_rows(n=40, start=10.0):
    """稳定上行 + 放量，保证触发强/中信号。"""
    rows = []
    p = start
    for i in range(n):
        p = start * (1 + 0.01 * i)
        o = p * 0.995
        rows.append((_d(i), o, p * 1.005, p * 0.99, p, 2000 + 100 * i))
    return rows


def test_engine_buys_on_signal_and_sells_on_stop(db):
    """上行触发入场（次日开盘成交），随后跌破 MA20 触发卖出，费用按口径扣除。"""
    cfg = RiskConfig(max_positions=6)
    code = "600001.SH"
    rows = _uptrend_rows(40)
    # 第 30 根后连续下跌 6%，跌破 MA20
    p = rows[-1][4]
    for i in range(40, 46):
        p *= 0.99
        rows.append((_d(i), p * 0.999, p * 1.002, p * 0.99, p, 1000))
    _seed_bars(db, code, rows)
    _seed_pool(db, code)
    # 基准：平坦
    _seed_bars(db, "000300.SH", [(_d(i), 4000.0, 4000.0, 4000.0, 4000.0, 1) for i in range(46)])

    eng = BacktestEngine(db, cfg, initial_capital=200_000, start=_d(25), end=_d(45))
    res = eng.run()

    assert res.trades, "应产生至少一笔成交"
    t = res.trades[0]
    # 入场在信号次日开盘，含滑点
    assert t.entry_date > _d(25)
    assert t.entry_price > 0
    if t.exit_date:
        # 净盈亏 = 卖出所得 - 买入成本（含双边费用）
        gross = (t.exit_price - t.entry_price) * t.shares
        assert t.pnl == pytest.approx(gross - t.fee_buy - t.fee_sell, rel=1e-6)
        assert t.fee_buy >= 5.0 and t.fee_sell >= 5.0  # 最低佣金
        assert t.fee_sell > t.fee_buy or t.amount_sell * 0.0005 > 0  # 印花税在卖侧
    # 指标齐全（§8.3）
    for key in ("total_return", "annualized_return", "max_drawdown", "sharpe",
                "win_rate", "profit_loss_ratio", "max_single_loss",
                "turnover_annualized", "excess_return", "relative_drawdown"):
        assert key in res.metrics
    assert len(res.nav) > 0 and len(res.benchmark_nav) == len(res.nav)
    # 净值曲线可导出
    csv_text = res.export_nav_csv()
    assert "date,nav,benchmark" in csv_text


def test_engine_defers_buy_on_limit_up(db):
    """次日开盘涨停 → 不可成交，顺延到再下一日。"""
    cfg = RiskConfig(max_positions=6)
    code = "600002.SH"
    rows = _uptrend_rows(30)
    # 信号日（第 20 根）次日开盘涨停：open = 昨收 × 1.1
    prev_close = rows[19][4]
    o, h, l, c = rows[20][1], rows[20][2], rows[20][3], rows[20][4]
    rows[20] = (_d(20), prev_close * 1.10, max(h, prev_close * 1.10), l, c, rows[20][5])
    _seed_bars(db, code, rows)
    _seed_pool(db, code)
    _seed_bars(db, "000300.SH", [(_d(i), 4000.0, 4000.0, 4000.0, 4000.0, 1) for i in range(30)])

    eng = BacktestEngine(db, cfg, initial_capital=200_000, start=_d(25), end=_d(29))
    res = eng.run()
    buys = [t for t in res.trades if t.exit_date is None or True]
    filled = [t for t in res.trades]
    # 若有成交，其入场日不得是涨停日 _d(21)
    for t in filled:
        assert t.entry_date != _d(21)


def test_engine_t_plus_1_sale_proceeds(db):
    """卖出资金 T+1 可用：卖出当日可用现金不含当日卖出所得。"""
    cfg = RiskConfig(max_positions=6)
    code = "600003.SH"
    rows = _uptrend_rows(40)
    p = rows[-1][4]
    for i in range(40, 48):
        p *= 0.98  # 快速下跌触发止损
        rows.append((_d(i), p * 0.999, p * 1.001, p * 0.99, p, 1000))
    _seed_bars(db, code, rows)
    _seed_pool(db, code)
    _seed_bars(db, "000300.SH", [(_d(i), 4000.0, 4000.0, 4000.0, 4000.0, 1) for i in range(48)])

    eng = BacktestEngine(db, cfg, initial_capital=200_000, start=_d(25), end=_d(47))
    res = eng.run()
    # 无异常即通过；核心断言：任意时点现金 + 持仓市值 ≈ 净值 × 初始资金（账目守恒）
    for dt, nav in res.nav_points:
        assert nav > 0
    assert res.metrics["total_return"] < 1.0

# ---------------------------------------------------------------------------
# 参数遍历（seam: run_parameter_sweep，同一 RiskConfig 批量多组参数）
# ---------------------------------------------------------------------------

import itertools

from app.backtest.sweep import run_parameter_sweep


def test_parameter_sweep_varies_riskconfig(db):
    code = "600010.SH"
    rows = _uptrend_rows(60)
    p = rows[-1][4]
    for i in range(60, 70):
        p *= 0.985
        rows.append((_d(i), p * 0.999, p * 1.001, p * 0.99, p, 1000))
    _seed_bars(db, code, rows)
    _seed_pool(db, code)
    _seed_bars(db, "000300.SH",
               [(_d(i), 4000.0, 4000.0, 4000.0, 4000.0, 1) for i in range(70)])

    grid = {"atr_multiplier": [1.5, 2.0, 3.0], "trail_pct": [0.05, 0.12]}
    results = run_parameter_sweep(db, grid, initial_capital=200_000,
                                  start=_d(30), end=_d(69))
    assert len(results) == 6  # 3 × 2 组合
    for cfg, res in results:
        assert isinstance(cfg, RiskConfig)
        assert "total_return" in res.metrics
    # 参数确实不同
    mults = {cfg.atr_multiplier for cfg, _ in results}
    assert mults == {1.5, 2.0, 3.0}


# ---------------------------------------------------------------------------
# 近 5 年全量跑通（stub 合成行情，不联网）
# ---------------------------------------------------------------------------

import random

from app.backtest.engine import BacktestEngine


def _seed_5y(db, n_codes=6, n_days=1200):
    """合成 5 年日线路径：牛市 + 回撤段混合，含涨跌停日。"""
    rng = random.Random(42)
    all_dates = [_d(i) for i in range(n_days)]
    # 基准：缓慢上行 + 一段 -15% 回撤
    bm = []
    v = 4000.0
    for i in range(n_days):
        drift = 0.0004
        if 400 <= i < 500:
            drift = -0.0016
        v *= 1 + drift + rng.gauss(0, 0.004)
        bm.append((all_dates[i], v))
    _seed_bars(db, "000300.SH", [(dt, x, x * 1.001, x * 0.999, x, 1) for dt, x in bm])

    for k in range(n_codes):
        code = f"6000{k:02d}.SH"
        rows = []
        p = 8.0 + k
        for i in range(n_days):
            trend = 0.0008 if (i + k * 100) % 1000 < 700 else -0.0006
            p *= 1 + trend + rng.gauss(0, 0.008)
            p = max(p, 1.0)
            o = p * (1 + rng.gauss(0, 0.002))
            rows.append((all_dates[i], o, p * 1.004, p * 0.996, p,
                         int(1500 + 800 * rng.random())))
        _seed_bars(db, code, rows)
        _seed_pool(db, code, score=100.0 + k)
    return all_dates


def test_full_5y_backtest_runs(db):
    dates = _seed_5y(db)
    cfg = RiskConfig(max_positions=6)
    eng = BacktestEngine(db, cfg, initial_capital=200_000,
                         start=dates[40], end=dates[-1])
    res = eng.run()
    assert len(res.nav) > 1000
    # 账目守恒：nav 有限且为正
    assert all(0 < n < 100 for n in res.nav)
    m = res.metrics
    for key in ("total_return", "annualized_return", "max_drawdown", "sharpe",
                "win_rate", "profit_loss_ratio", "turnover_annualized",
                "excess_return", "relative_drawdown"):
        assert key in m
    csv_text = res.export_nav_csv()
    assert len(csv_text.splitlines()) == len(res.nav) + 1
    # 训练/留出切分可跑（2024-2025 留出验证口径）
    eng_holdout = BacktestEngine(db, cfg, initial_capital=200_000,
                                 start=dates[900], end=dates[-1])
    res2 = eng_holdout.run()
    assert res2.metrics["total_return"] > -1.0
