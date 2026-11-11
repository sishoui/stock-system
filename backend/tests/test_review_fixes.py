"""Code-review 收尾修复批次测试（行业封顶 / H4 豁免 / H5 口径 / 新股豁免 /
指纹版本 / 公共名 / 取价收拢 / 初始资金 / 调度接线）。

Seam：
- BacktestEngine.run()：同行业 30% 封顶按 stock_profile.board 真实分桶
- run_screening()：H4 金融/地产行业豁免（行业来自数据层）
- filters.check_hard_gates：H5 净利润 ≤ 0 保守剔除口径
- checks.abnormal_check：新股（上市未满 N 交易日）涨跌幅豁免
- service.fingerprint：PROMPT_VERSION 变化触发指纹变化
- PriceQuery.get_close / get_open_with_prev_close：取价 SQL 收拢
- RiskConfig.initial_capital：初始资金单一来源
- data.scheduler.build_scheduler：AI 全池评估与规则引擎任务接线
"""
from __future__ import annotations

from datetime import date, timedelta
from unittest import mock

import pytest
from sqlalchemy import text

from app.common.config import DataConfig, RiskConfig
from app.data.db import Database

BASE = date(2021, 1, 4)


def _d(i: int) -> str:
    return (BASE + timedelta(days=i)).isoformat()


@pytest.fixture
def db(tmp_path) -> Database:
    d = Database(f"sqlite:///{tmp_path / 'review.db'}")
    d.create_all()
    return d


def _seed_bars(db, code, rows):
    with db.session() as s:
        for dt, o, h, l, c, v in rows:
            s.execute(text(
                "INSERT INTO daily_bar(code,date,open,high,low,close,volume,amount)"
                " VALUES(:c,:d,:o,:h,:l,:cl,:v,:a)"
            ), {"c": code, "d": dt, "o": o, "h": h, "l": l, "cl": c, "v": v, "a": c * v})
        s.commit()


def _uptrend_rows(n=40, start=10.0):
    rows = []
    p = start
    for i in range(n):
        p = start * (1 + 0.01 * i)
        o = p * 0.995
        rows.append((_d(i), o, p * 1.005, p * 0.99, p, 2000 + 100 * i))
    return rows


def _seed_profile(db, code, board, list_date="2015-01-01"):
    from app.data.db_models import StockProfile

    with db.session() as s:
        s.add(StockProfile(code=code, name="公司" + code, board=board,
                           list_date=list_date, is_st=False))
        s.commit()


def _seed_pool(db, code, score=100.0):
    from app.strategy.pool_models import PoolBase, PoolMembership

    PoolBase.metadata.create_all(db.engine)
    with db.session() as s:
        s.add(PoolMembership(code=code, entered_at=_d(0), rank=1, score=score,
                             ai_sustainability=None, reason="new_report"))
        s.commit()


def _seed_ai_positive(db, code):
    """AI 可持续性正面 → 入场信号可升为强（仓位 1.0×）。"""
    from app.data.db_models import AiAssessment

    with db.session() as s:
        s.add(AiAssessment(
            code=code, assessed_at=_d(0), authenticity="pass",
            authenticity_confidence=0.9, timeliness_valid_until=_d(300),
            sustainability_score=80.0, sustainability_trend="improving",
            model_version="stub", data_fingerprint="fp-" + code,
            payload_json="{}", review_round=0))
        s.commit()


# ---------------------------------------------------------------- 1 行业封顶

def test_industry_cap_buckets_by_real_industry(db, tmp_path):
    """同行业（board）3 只强信号：封顶 0.20 只放 2 只；跨行业 3 只全放。"""
    from app.backtest.engine import BacktestEngine

    codes = ["600001.SH", "600002.SH", "600003.SH"]
    for c in codes:
        rows = _uptrend_rows(40)
        p = rows[-1][4]
        for i in range(40, 47):
            p *= 0.95  # 后期下跌触发止损/离场，确保已平仓交易入 trades
            rows.append((_d(i), p * 0.999, p * 1.001, p * 0.99, p, 1000))
        _seed_bars(db, c, rows)
        _seed_pool(db, c)
        _seed_profile(db, c, board="银行")
        _seed_ai_positive(db, c)
    _seed_bars(db, "000300.SH",
               [(_d(i), 4000.0, 4000.0, 4000.0, 4000.0, 1) for i in range(47)])

    cfg = RiskConfig(max_positions=6, max_industry_weight=0.20)
    res = BacktestEngine(db, cfg, initial_capital=200_000,
                         start=_d(25), end=_d(46)).run()
    buys = {t.code for t in res.trades}
    assert len(buys) == 2, f"同行业 3 只应只成交 2 只，实际 {sorted(buys)}"

    # 对照组：行业不同 → 3 只全部入场
    db2 = Database(f"sqlite:///{tmp_path / 'ctrl.db'}")
    db2.create_all()
    for i, c in enumerate(codes):
        rows = _uptrend_rows(40)
        p = rows[-1][4]
        for k in range(40, 47):
            p *= 0.95
            rows.append((_d(k), p * 0.999, p * 1.001, p * 0.99, p, 1000))
        _seed_bars(db2, c, rows)
        _seed_pool(db2, c)
        _seed_profile(db2, c, board=f"行业{i}")
        _seed_ai_positive(db2, c)
    _seed_bars(db2, "000300.SH",
               [(_d(i), 4000.0, 4000.0, 4000.0, 4000.0, 1) for i in range(47)])
    res2 = BacktestEngine(db2, cfg, initial_capital=200_000,
                          start=_d(25), end=_d(46)).run()
    assert len({t.code for t in res2.trades}) == 3


# ---------------------------------------------------------------- 2 H4 豁免

def _seed_finance(db, code, debt_ratio):
    from app.data.db_models import FinanceIndicator

    with db.session() as s:
        for y in (2021, 2022, 2023):
            s.add(FinanceIndicator(
                code=code, report_period=f"{y}-12-31", roe=10.0, roe_ttm=12.0,
                debt_ratio=debt_ratio, operating_cashflow=100.0, net_profit=150.0))
        s.commit()


def test_h4_exemption_uses_real_industry(db):
    """银行股负债率 85% 不触发 H4；主板同负债率触发。"""
    from app.strategy.engine import run_screening

    _seed_profile(db, "600001.SH", board="银行")
    _seed_finance(db, "600001.SH", debt_ratio=85.0)
    _seed_profile(db, "600002.SH", board="主板")
    _seed_finance(db, "600002.SH", debt_ratio=85.0)

    res = run_screening(db, "2024-06-01")
    excluded = {e.code: e.reasons for e in res.excluded}
    assert "H4" not in excluded.get("600001.SH", [])
    assert "H4" in excluded.get("600002.SH", [])


# ---------------------------------------------------------------- 3 H5 口径

def test_h5_negative_net_profit_excluded_conservatively():
    from app.strategy.filters import check_hard_gates

    def factors(**kw):
        base = dict(code="x", is_st=False, list_years=5.0, roe_ttm=10.0,
                    roe_3y_min=6.0, debt_ratio=50.0, industry="主板",
                    operating_cashflow=10.0, net_profit=100.0)
        base.update(kw)
        return check_hard_gates(**base)

    # 净利润为负/为零：比值无意义 → 保守剔除（现金流为正也不豁免）
    assert factors(net_profit=-50.0) == ["H5"]
    assert factors(net_profit=0.0) == ["H5"]
    # 净利润为正且比值达标 → 通过
    assert factors(operating_cashflow=60.0, net_profit=100.0) == []
    # 净利润缺失：无法判定比值，不剔除（维持原口径）
    assert factors(net_profit=None) == []


# ---------------------------------------------------------------- 4 新股豁免

def test_abnormal_check_exempts_new_listing(db):
    from app.data.checks import abnormal_check
    from app.data.db_models import DailyBar

    # 老股：250 天历史后暴涨 60% → 告警
    old = []
    p = 10.0
    for i in range(250):
        p *= 1.001
        old.append((_d(i), p, p * 1.002, p * 0.998, p, 1000))
    old.append((_d(250), p, p * 1.7, p, p * 1.6, 1000))  # +60%
    _seed_bars(db, "600001.SH", old)

    # 新股：仅 10 天历史，同样暴涨 60% → 豁免
    new = []
    q = 10.0
    for i in range(241, 250):
        q *= 1.001
        new.append((_d(i), q, q * 1.002, q * 0.998, q, 1000))
    new.append((_d(250), q, q * 1.7, q, q * 1.6, 1000))
    _seed_bars(db, "600002.SH", new)

    issues = abnormal_check(db, _d(250), pct_limit=0.44, exempt_trading_days=244)
    flagged = {c for c, _ in issues if "涨跌幅" in _}
    assert "600001.SH" in flagged
    assert "600002.SH" not in flagged


def test_dataconfig_new_stock_exempt_default():
    c = DataConfig()
    assert c.new_stock_exempt_trading_days == 244  # 上市首年


# ---------------------------------------------------------------- 5 指纹版本

def test_fingerprint_changes_with_prompt_version():
    import app.analysis.ai.prompts as prompts
    from app.analysis.ai.service import fingerprint

    fp1 = fingerprint({"a": 1}, "model-x")
    with mock.patch.object(prompts, "PROMPT_VERSION", "test-bump"):
        fp2 = fingerprint({"a": 1}, "model-x")
    assert fp1 != fp2


def test_prompt_version_constant_declared():
    from app.analysis.ai.prompts import PROMPT_VERSION

    assert isinstance(PROMPT_VERSION, str) and PROMPT_VERSION


# ---------------------------------------------------------------- 6 公共名

def test_service_public_names_importable():
    from app.analysis.ai.service import (  # noqa: F401
        parse_llm_output,
        persist_assessment,
        persist_degraded_assessment,
    )


# ---------------------------------------------------------------- 7 取价收拢

def test_price_query_close_and_open_with_prev_close(db):
    from app.data.price_query import PriceQuery

    _seed_bars(db, "600001.SH", [
        (_d(0), 10.0, 10.5, 9.5, 10.0, 100),
        (_d(1), 11.0, 11.5, 10.5, 11.0, 100),
        (_d(2), 12.0, 12.5, 11.5, 12.0, 100),
    ])
    pq = PriceQuery(db)
    assert pq.get_close("600001.SH", _d(1)) == 11.0
    assert pq.get_close("600001.SH") == 12.0          # 最新
    assert pq.get_close("600001.SH", on_or_before=_d(1)) == 11.0
    o, prev = pq.get_open_with_prev_close("600001.SH", _d(2))
    assert (o, prev) == (12.0, 11.0)
    assert pq.get_open_with_prev_close("600001.SH", _d(0)) == (10.0, None)


# ---------------------------------------------------------------- 8 初始资金

def test_riskconfig_initial_capital_default_and_paper_wiring(db):
    assert RiskConfig().initial_capital == 200_000.0
    from app.backtest.paper import PaperTradingEngine

    eng = PaperTradingEngine(db, RiskConfig(initial_capital=500_000.0))
    assert eng.initial_capital == 500_000.0
    assert eng.state.cash == 500_000.0


# ---------------------------------------------------------------- 9 调度接线

def test_scheduler_registers_ai_and_rule_jobs():
    from app.data.scheduler import build_scheduler

    sched = build_scheduler(
        daily_job=lambda: None, finance_full_job=lambda: None,
        ai_pool_job=lambda: None, rule_engine_job=lambda: None,
    )
    jobs = {j.id for j in sched.get_jobs()}
    assert "ai_pool_assess" in jobs
    assert "rule_engine_orders" in jobs
    sched.remove_all_jobs()


def test_scheduler_without_ai_jobs_omits_them():
    from app.data.scheduler import build_scheduler

    sched = build_scheduler(daily_job=lambda: None, finance_full_job=lambda: None)
    jobs = {j.id for j in sched.get_jobs()}
    assert "ai_pool_assess" not in jobs
    assert "rule_engine_orders" not in jobs
    sched.remove_all_jobs()
