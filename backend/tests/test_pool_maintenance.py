"""Issue #21：股票池动态维护。stub 数据 + 临时 SQLite。

seam：
- pool.rebuild_pool（全量重算重建全池）—— 捕捉入池数/入池时间/变更留痕
- pool.run_incremental_removal（月度/事件驱动剔除）—— 捕捉 ST/AI fail/硬门槛即时移出
- pool.rank_pool（池内排序含 AI 可持续性加权）—— 捕捉 AI 缺失不阻塞
"""
from __future__ import annotations

import pytest

from app.data.db import Database
from app.data.db_models import AiAssessment, FinanceIndicator, StockProfile, Valuation
from app.strategy.pool import (
    POOL_SIZE_DEFAULT,
    rank_pool,
    rebuild_pool,
    run_incremental_removal,
)
from app.strategy.pool_models import PoolChangeLog, PoolMembership


@pytest.fixture
def db(tmp_path) -> Database:
    d = Database(f"sqlite:///{tmp_path / 's21.db'}")
    d.create_all()
    return d


def _seed(db, code, *, is_st=False, roe_ttm=15.0, list_date="2000-01-01"):
    with db.session() as s:
        s.add(StockProfile(code=code, name="公司" + code, board="主板",
                           list_date=list_date, is_st=is_st, status_history=""))
        s.add(FinanceIndicator(code=code, report_period="2023-12-31",
                               roe=roe_ttm, roe_ttm=roe_ttm, debt_ratio=40.0,
                               operating_cashflow=100.0, net_profit=100.0,
                               revenue_growth=10.0, profit_growth=10.0))
        s.add(Valuation(code=code, date="2024-03-31", pe=15.0, pe_ttm=15.0, pb=2.0))
        s.commit()


def _seed_ai(db, code, assessed_at, authenticity="pass", sustainability=80.0):
    with db.session() as s:
        s.add(AiAssessment(code=code, assessed_at=assessed_at,
                           authenticity=authenticity, authenticity_confidence=0.9,
                           sustainability_score=sustainability, sustainability_trend="stable",
                           model_version="test-model", data_fingerprint="fp-" + code))
        s.commit()


def _members(db):
    with db.session() as s:
        return {m.code: m for m in s.query(PoolMembership).all()}


class TestRebuildPool:
    """seam: rebuild_pool —— 全量重算后池规模 = N，且每只带入池时间。"""

    def test_pool_holds_n_with_entered_at(self, db):
        for i in range(85):
            _seed(db, f"6000{i:02d}.SH")
        result = rebuild_pool(db, "2024-03-31")
        members = _members(db)
        assert len(members) == POOL_SIZE_DEFAULT == 80
        assert result.entered == sorted(members)
        # 排名前 80 的进入，后 5 名落选
        scores = {r.code: r.score for r in result.ranked}
        picked = {r.code for r in result.ranked[:80]}
        assert picked == set(members)
        assert all(m.entered_at == "2024-03-31" for m in members.values())
        # 全量重建清空旧池再入（入池时间刷新）
        rebuild_pool(db, "2024-04-15")
        assert all(m.entered_at == "2024-04-15" for m in _members(db).values())
        # 变更留痕：首次重建记录 80 条 enter 日志
        with db.session() as s:
            enters = s.query(PoolChangeLog).filter(PoolChangeLog.action == "enter").all()
            assert len(enters) == 80
            assert all(e.date == "2024-03-31" for e in enters)

    def test_n_configurable(self, db):
        _seed(db, "600001.SH")
        _seed(db, "600002.SH")
        result = rebuild_pool(db, "2024-03-31", n=1)
        assert len(result.entered) == 1


class TestIncrementalRemoval:
    """seam: run_incremental_removal —— ST/退市风险/财报异常/AI fail/暴雷即时移出并留痕。"""

    def _build(self, db):
        for i in range(5):
            _seed(db, f"60000{i}.SH")
        rebuild_pool(db, "2024-03-31", n=4)

    def test_ai_fail_removed_same_day_with_log(self, db):
        self._build(db)
        code = "600000.SH"
        _seed_ai(db, code, "2024-04-01", authenticity="fail")
        removed = run_incremental_removal(db, "2024-04-01")
        assert code in removed
        assert code not in _members(db)
        with db.session() as s:
            log = (s.query(PoolChangeLog)
                   .filter(PoolChangeLog.code == code, PoolChangeLog.action == "remove")
                   .order_by(PoolChangeLog.id.desc()).first())
            assert log is not None and log.date == "2024-04-01"
            assert "ai_authenticity_fail" in log.reason

    def test_new_st_removed(self, db):
        self._build(db)
        with db.session() as s:
            p = s.query(StockProfile).filter(StockProfile.code == "600001.SH").one()
            p.is_st = True
            s.commit()
        removed = run_incremental_removal(db, "2024-04-01")
        assert "600001.SH" in removed

    def test_financial_anomaly_removed(self, db):
        self._build(db)
        # 暴雷：最新财报 ROE 转为深度负值 + 净利大幅下滑（财报异常）
        with db.session() as s:
            s.add(FinanceIndicator(code="600002.SH", report_period="2024-03-31",
                                   roe=-30.0, roe_ttm=-30.0, debt_ratio=90.0,
                                   operating_cashflow=-50.0, net_profit=-1e8,
                                   profit_growth=-200.0))
            s.commit()
        removed = run_incremental_removal(db, "2024-04-01")
        assert "600002.SH" in removed

    def test_healthy_members_stay(self, db):
        self._build(db)
        removed = run_incremental_removal(db, "2024-04-01")
        assert removed == []

    def test_delisting_risk_removed(self, db):
        self._build(db)
        with db.session() as s:
            p = s.query(StockProfile).filter(StockProfile.code == "600003.SH").one()
            p.delist_date = "2024-12-31"
            p.status_history = "退市风险警示"
            s.commit()
        removed = run_incremental_removal(db, "2024-04-01")
        assert "600003.SH" in removed


class TestRankAndTurnover:
    """seam: rank_pool / 增量换仓 —— 后 30% 跌出；AI 可持续性参与排序且缺失不阻塞。"""

    def test_ai_sustainability_reorders(self, db):
        # 三只票基本面分接近，AI 可持续性高低决定池内排名
        for i, sus in zip(range(3), (90.0, 20.0, None)):
            _seed(db, f"60000{i}.SH")
            if sus is not None:
                _seed_ai(db, f"60000{i}.SH", "2024-03-30", sustainability=sus)
        rebuild_pool(db, "2024-03-31", n=3)
        ranked = rank_pool(db, "2024-03-31", ai_weight=0.3)
        # AI 可持续性高者第一；低分 AI（20）被加权拉低到最后；AI 缺失视为中性不被惩罚
        assert ranked[0].code == "600000.SH"
        assert ranked[-1].code == "600001.SH"
        # AI 缺失的不阻塞（仍在池内且排在低 AI 者之前）
        assert [r.code for r in ranked] == ["600000.SH", "600002.SH", "600001.SH"]

    def test_ai_missing_weight_zero_not_blocked(self, db):
        _seed(db, "600000.SH")
        rebuild_pool(db, "2024-03-31", n=1)
        ranked = rank_pool(db, "2024-03-31", ai_weight=0.3)
        assert len(ranked) == 1  # 未评仍进池、参与排序

    def test_bottom_30pct_drops_out_on_rebuild_cycle(self, db):
        # 10 只入池，随后新财报季重算：排名后 30% / 触硬门槛跌出
        for i in range(10):
            _seed(db, f"60000{i}.SH")
        rebuild_pool(db, "2024-01-15", n=10)
        # 末位 3 只基本面恶化（跌入后 30% 并触硬门槛）
        for i in (7, 8, 9):
            with db.session() as s:
                f = (s.query(FinanceIndicator)
                     .filter(FinanceIndicator.code == f"60000{i}.SH").one())
                f.roe_ttm = f.roe = 1.0
                s.commit()
        result = rebuild_pool(db, "2024-03-31", n=10)
        removed = result.removed
        assert {"600007.SH", "600008.SH", "600009.SH"} <= set(removed)
        assert len(_members(db)) == 7  # 不足 N 不回补劣质票
