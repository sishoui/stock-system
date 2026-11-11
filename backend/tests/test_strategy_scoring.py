"""Issue #20：硬门槛 H1–H5 与三维打分（质量40/成长30/估值30）。stub 数据 + 临时 SQLite。"""
from __future__ import annotations

from datetime import date

import pytest

from app.data.db import Database
from app.data.db_models import FinanceIndicator, StockProfile, Valuation
from app.strategy.engine import run_screening
from app.strategy.filters import check_hard_gates
from app.strategy.scoring import (
    cross_section_percentile,
    historical_percentile,
    normalize_rank_0_100,
)


@pytest.fixture
def db(tmp_path) -> Database:
    d = Database(f"sqlite:///{tmp_path / 's20.db'}")
    d.create_all()
    return d


def _seed_profile(db, code, name="测试", list_date="2000-01-01", is_st=False, industry=None):
    with db.session() as s:
        s.add(StockProfile(code=code, name=name, board="主板", list_date=list_date,
                           is_st=is_st, status_history=""))
        s.commit()


def _seed_finance(db, code, report_period, roe_ttm=None, roe=None, debt_ratio=None,
                  opcf=None, net_profit=None, revenue_growth=None, profit_growth=None,
                  gross_margin=None, roe_series=None):
    """roe_series: 同一 code 的多年年报 ROE 会被覆盖，故只在必要时用循环 seed。"""
    with db.session() as s:
        s.add(FinanceIndicator(
            code=code, report_period=report_period,
            roe=roe, roe_ttm=roe_ttm, debt_ratio=debt_ratio,
            operating_cashflow=opcf, net_profit=net_profit,
            revenue_growth=revenue_growth, profit_growth=profit_growth,
            gross_margin=gross_margin,
        ))
        s.commit()


def _seed_valuation(db, code, d, pe=None, pb=None):
    with db.session() as s:
        s.add(Valuation(code=code, date=d, pe=pe, pe_ttm=pe, pb=pb))
        s.commit()


class TestHardGates:
    """seam: filters.check_hard_gates —— 捕捉规则判定与剔除理由；不捕捉 SQL。"""

    def _factors(self, **kw):
        base = dict(
            code="600519.SH", is_st=False, list_years=3.0,
            roe_ttm=15.0, roe_3y_min=10.0, debt_ratio=40.0,
            industry="食品饮料",
            operating_cashflow=100.0, net_profit=100.0,
        )
        base.update(kw)
        return base

    def test_pass_all(self):
        reasons = check_hard_gates(**self._factors())
        assert reasons == []

    def test_h1_st(self):
        assert check_hard_gates(**self._factors(is_st=True)) == ["H1"]

    def test_h2_listed_less_than_2_years(self):
        assert check_hard_gates(**self._factors(list_years=1.5)) == ["H2"]

    def test_h3_roe_ttm_below_8(self):
        assert check_hard_gates(**self._factors(roe_ttm=7.9)) == ["H3"]

    def test_h3_annual_roe_below_5(self):
        assert check_hard_gates(**self._factors(roe_3y_min=4.0)) == ["H3"]

    def test_h4_debt_ratio_ge_70(self):
        assert check_hard_gates(**self._factors(debt_ratio=70.0)) == ["H4"]

    def test_h4_financial_industry_exemption(self):
        assert check_hard_gates(**self._factors(debt_ratio=90.0, industry="银行")) == []

    def test_h4_real_estate_industry_exemption(self):
        assert check_hard_gates(**self._factors(debt_ratio=80.0, industry="房地产")) == []

    def test_h5_cashflow_zero(self):
        assert check_hard_gates(**self._factors(operating_cashflow=-1.0)) == ["H5"]

    def test_h5_cashflow_below_half_profit(self):
        assert check_hard_gates(**self._factors(operating_cashflow=40.0, net_profit=100.0)) == ["H5"]

    def test_h5_no_profit_excluded_conservatively(self):
        # 净利为负/为零：比值无意义 → 保守口径直接剔除（GLOSSARY H5 口径）
        assert check_hard_gates(**self._factors(net_profit=-50.0, operating_cashflow=10.0)) == ["H5"]
        assert check_hard_gates(**self._factors(net_profit=0.0, operating_cashflow=10.0)) == ["H5"]

    def test_multiple_gates_sorted(self):
        r = check_hard_gates(**self._factors(is_st=True, roe_ttm=1.0))
        assert r == ["H1", "H3"]


class TestNormalization:
    """seam: scoring 归一化函数 —— 排序稳定性与 0~100 范围。"""

    def test_normalize_rank_range_and_order(self):
        vals = [10.0, 20.0, 30.0, 40.0]
        out = normalize_rank_0_100(vals, higher_is_better=True)
        assert out[0] == 0.0 and out[-1] == 100.0
        assert out == sorted(out)

    def test_normalize_lower_is_better(self):
        vals = [10.0, 20.0, 30.0]
        out = normalize_rank_0_100(vals, higher_is_better=False)
        assert out[0] == 100.0 and out[-1] == 0.0

    def test_normalize_stable_identical_input(self):
        vals = [1.0, 2.0, 3.0]
        a = normalize_rank_0_100(vals, True)
        b = normalize_rank_0_100(list(vals), True)
        assert a == b

    def test_cross_section_percentile(self):
        vals = {i: v for i, v in enumerate([10.0, 20.0, 30.0, 40.0])}
        assert cross_section_percentile(10.0, vals) == pytest.approx(0.0)
        assert cross_section_percentile(40.0, vals) == pytest.approx(100.0)
        assert cross_section_percentile(25.0, vals) == pytest.approx(50.0)

    def test_historical_percentile(self):
        hist = [1.0, 2.0, 3.0, 4.0]
        assert historical_percentile(4.0, hist) == pytest.approx(100.0)
        assert historical_percentile(1.0, hist) == pytest.approx(0.0)
        assert historical_percentile(2.5, hist) == pytest.approx(50.0)


class TestEngine:
    """seam: engine.run_screening(db, as_of) —— 端到端行为：剔除理由 + 排序稳定。"""

    def test_gates_remove_with_reason(self, db):
        _seed_profile(db, "600519.SH", name="茅台")
        _seed_profile(db, "000001.SZ", name="ST股", is_st=True)
        for code in ("600519.SH", "000001.SZ"):
            _seed_finance(db, code, "2024-12-31", roe_ttm=15.0, roe=15.0,
                          debt_ratio=30.0, opcf=100.0, net_profit=100.0)
        result = run_screening(db, as_of="2025-01-01")
        assert [r.code for r in result.excluded if r.code == "000001.SZ"]
        ex = next(r for r in result.excluded if r.code == "000001.SZ")
        assert "H1" in ex.reasons

    def test_ranked_list_stable_and_weighted(self, db):
        codes = ["600001.SH", "600002.SH", "600003.SH"]
        roes = [10.0, 15.0, 20.0]
        for code, roe in zip(codes, roes):
            _seed_profile(db, code)
            _seed_finance(db, code, "2024-12-31", roe_ttm=roe, roe=roe,
                          debt_ratio=30.0, opcf=1000.0, net_profit=1000.0)
            _seed_valuation(db, code, "2024-12-31", pe=20.0, pb=2.0)
        r1 = run_screening(db, as_of="2025-01-01")
        r2 = run_screening(db, as_of="2025-01-01")
        assert [x.code for x in r1.ranked] == [x.code for x in r2.ranked]
        # ROE 更高者质量分更高，在其余因素相同时总分更高
        assert r1.ranked[-1].code == "600001.SH"
        assert r1.ranked[0].code == "600003.SH"
        top = r1.ranked[0]
        assert 0.0 <= top.score <= 100.0
        assert top.quality is not None and top.growth is not None and top.valuation is not None
        assert top.reasons  # 带理由

    def test_valuation_uses_percentile_mix(self, db):
        # 估值分 = 历史 5 年分位与行业内横截面分位各半
        codes = ["600001.SH", "600002.SH"]
        for code in codes:
            _seed_profile(db, code)
            _seed_finance(db, code, "2024-12-31", roe_ttm=15.0, roe=15.0,
                          debt_ratio=30.0, opcf=1000.0, net_profit=1000.0)
        # 历史 5 年 PE：code1 一直贵（当前也是最高 → 历史分位 100），code2 一直 20
        _seed_valuation(db, "600001.SH", "2024-12-31", pe=40.0, pb=2.0)
        _seed_valuation(db, "600002.SH", "2024-12-31", pe=10.0, pb=1.0)
        for y in range(2020, 2024):
            _seed_valuation(db, "600001.SH", f"{y}-12-31", pe=40.0, pb=2.0)
            _seed_valuation(db, "600002.SH", f"{y}-12-31", pe=10.0, pb=1.0)
        r = run_screening(db, as_of="2025-01-01")
        by_code = {x.code: x for x in r.ranked}
        # code2 便宜：历史与横截面分位都低 → 估值分高
        assert by_code["600002.SH"].valuation > by_code["600001.SH"].valuation

    def test_ai_weight_zero_interface_reserved(self, db):
        _seed_profile(db, "600519.SH")
        _seed_finance(db, "600519.SH", "2024-12-31", roe_ttm=15.0, roe=15.0,
                      debt_ratio=30.0, opcf=100.0, net_profit=100.0)
        r = run_screening(db, as_of="2025-01-01")
        assert r.weights == {"quality": 0.4, "growth": 0.3, "valuation": 0.3, "ai": 0.0}

    def test_missing_data_excluded(self, db):
        _seed_profile(db, "600999.SH")
        r = run_screening(db, as_of="2025-01-01")
        assert any(x.code == "600999.SH" for x in r.excluded)
