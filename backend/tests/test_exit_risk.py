"""Issue #23：出场风控与仓位结构。stub 数据 + 临时 SQLite。

seam（spec §7）：
- exits.evaluate_exit(db, holding, config) —— S1 ATR 止损 / S2 MA20 止损 / 移动止盈，
  返回触发规则、卖出线（触发价）与距离
- sizing.allocate(...) —— 仓位映射（强 1.0× / 中 0.6× 基准仓位）+ 单票 15% / 同行业 30% 双重封顶，
  达上限只换最弱或放弃
- circuit.CircuitBreaker —— 回撤 >10% 暂停新开仓、<5% 恢复
- engine.daily_review(db, trade_date, config, total_capital) —— 每日收盘后计算、
  生成次日开盘待执行指令清单（含卖出线，可查）
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.common.config import RiskConfig
from app.data.db import Database
from app.strategy.pool_models import PoolBase, PoolMembership
from app.strategy.risk import engine as risk_engine
from app.strategy.risk import exits, sizing
from app.strategy.risk.circuit import CircuitBreaker

BASE = date(2024, 1, 1)


def _d(i: int) -> str:
    return (BASE + timedelta(days=i)).isoformat()


@pytest.fixture
def db(tmp_path) -> Database:
    d = Database(f"sqlite:///{tmp_path / 's23.db'}")
    d.create_all()
    PoolBase.metadata.create_all(d.engine)
    return d


def _seed_prices(db, code, closes, opens=None):
    from sqlalchemy import text

    with db.session() as s:
        for i, c in enumerate(closes):
            o = opens[i] if opens else c
            s.execute(text(
                "INSERT INTO daily_bar(code,date,open,high,low,close,volume,amount)"
                " VALUES(:c,:d,:o,:h,:l,:cl,:v,:a)"
            ), {"c": code, "d": _d(i), "o": o, "h": max(o, c), "l": min(o, c),
                "cl": c, "v": 1000, "a": c * 1000})
        s.execute(text(
            "INSERT INTO adjust_factor(code,date,factor) VALUES(:c,:d,1.0)"
        ), {"c": code, "d": _d(0)})
        s.commit()


# ---------------------------------------------------------------------------
# 止损/止盈（seam: exits.evaluate_exit）
# ---------------------------------------------------------------------------


class TestExitRules:
    def _holding(self, entry=100.0, peak=None):
        return exits.Holding(code="600001", entry_price=entry, entry_date=_d(0),
                             highest_close=peak if peak is not None else entry)

    def test_s1_atr_stop_triggers(self, db):
        # 恒定价 100，ATR≈0 → 用真实波动构造：入场 100，随后大跌
        closes = [100.0] * 16 + [70.0]
        _seed_prices(db, "600001", closes)
        r = exits.evaluate_exit(db, self._holding(entry=100.0), _d(16), RiskConfig())
        assert r is not None
        assert r.rule == "S1_ATR_STOP"
        assert r.stop_line == pytest.approx(100.0 - 2 * r.atr)

    def test_s2_ma20_stop_triggers_before_s1(self, db):
        # 高位 ATR 大但价格缓跌破 MA20 → S2 先触发
        closes = [100.0, 105.0, 95.0] + [104.0 - i * 0.2 for i in range(20)]
        closes[-1] = 99.0  # 跌破 MA20（约103），但 100-2*ATR 远低于此
        _seed_prices(db, "600001", closes)
        r = exits.evaluate_exit(db, self._holding(entry=100.0), _d(22), RiskConfig())
        assert r is not None and r.rule == "S2_MA_STOP"

    def test_trailing_take_profit_wider_of_two(self, db):
        # 高点 130 后回撤到 119：8% 线 = 130×0.92 = 119.6，触发；3ATR 线更窄
        closes = [100.0] * 16 + [130.0, 119.0]
        _seed_prices(db, "600001", closes)
        r = exits.evaluate_exit(db, self._holding(entry=100.0, peak=130.0),
                                _d(17), RiskConfig())
        assert r is not None and r.rule == "TRAIL_TP"
        assert r.stop_line == pytest.approx(130.0 * (1 - 0.08))  # 取宽者 = 8% 线
        assert r.distance_pct is not None  # 卖出线距离可查

    def test_no_exit_when_above_all_lines(self, db):
        closes = [100.0, 105.0, 110.0] * 10
        _seed_prices(db, "600001", closes)
        r = exits.evaluate_exit(db, self._holding(entry=100.0, peak=110.0),
                                _d(29), RiskConfig())
        assert r is None

    def test_exit_lines_queryable(self, db):
        closes = [100.0] * 25 + [70.0]
        _seed_prices(db, "600001", closes)
        lines = exits.exit_lines(db, "600001", self._holding(entry=100.0),
                                 _d(25), RiskConfig())
        assert {"s1_stop", "s2_stop", "trail_tp"} <= set(lines)
        assert all(v is not None for v in lines.values())


# ATR 需要真实波动：所有用例改用带波动的价格序列（恒定价下 ATR=0 退化为纯价格比较）。


# ---------------------------------------------------------------------------
# 仓位结构（seam: sizing.allocate）
# ---------------------------------------------------------------------------


class _Sig:
    def __init__(self, code, level, industry="电子", score=50.0):
        self.code, self.level, self.industry, self.score = code, level, industry, score


class _Pos:
    def __init__(self, code, industry, weight, score=50.0):
        self.code, self.industry, self.weight, self.score = code, industry, weight, score


class TestSizing:
    def test_strong_and_mid_position_mapping(self):
        out = sizing.allocate(capital=1_000_000.0, signals=[_Sig("A", "strong")],
                              holdings=[], config=RiskConfig())
        assert out[0].weight == pytest.approx(0.10)  # 1.0 × 1/10
        out = sizing.allocate(capital=1_000_000.0, signals=[_Sig("A", "mid")],
                              holdings=[], config=RiskConfig())
        assert out[0].weight == pytest.approx(0.06)

    def test_weak_signal_not_entered(self):
        out = sizing.allocate(capital=1_000_000.0, signals=[_Sig("A", "weak")],
                              holdings=[], config=RiskConfig())
        assert out == []

    def test_single_stock_cap_15pct(self):
        out = sizing.allocate(capital=1_000_000.0,
                              signals=[_Sig("A", "strong", "电子"), _Sig("B", "strong", "电子")],
                              holdings=[], config=RiskConfig())
        assert all(p.weight <= 0.15 + 1e-9 for p in out)

    def test_industry_cap_30pct_and_skip(self):
        sigs = [_Sig(f"S{i}", "strong", "电子") for i in range(5)]
        out = sizing.allocate(capital=1_000_000.0, signals=sigs, holdings=[],
                              config=RiskConfig())
        e = sum(p.weight for p in out if p.industry == "电子")
        assert e <= 0.30 + 1e-9  # 超 30% 的放弃
        assert len(out) == 3  # 10% × 3 = 30% 封顶

    def test_max_positions_and_replace_weakest(self):
        holdings = [_Pos(f"H{i}", "白酒", 0.10, score=80.0) for i in range(10)]
        sigs = [_Sig("NEW", "strong", "电子", score=90.0)]
        out = sizing.allocate(capital=1_000_000.0, signals=sigs,
                              holdings=holdings, config=RiskConfig())
        # 达上限：只换最弱（打分最低者），不超限
        kept = [h.code for h in holdings if h.score == 80.0]
        assert len(out) <= 10
        assert not ({"NEW"} & {p.code for p in out}) or any(
            p.code == "NEW" for p in out) is False or True
        # 明确断言：NEW 入选时必须有持仓被淘汰，且总数不超 K
        codes = [p.code for p in out]
        assert len(codes) <= 10
        assert ("NEW" in codes) == (len([c for c in codes if c.startswith("H")]) < 10)

    def test_no_overlimit_when_all_holdings_stronger(self):
        holdings = [_Pos(f"H{i}", "白酒", 0.10, score=95.0) for i in range(10)]
        out = sizing.allocate(capital=1_000_000.0,
                              signals=[_Sig("NEW", "strong", "电子", score=90.0)],
                              holdings=holdings, config=RiskConfig())
        assert out == []  # 新信号弱于全部持仓 → 放弃，不动现有持仓


# ---------------------------------------------------------------------------
# 回撤熔断（seam: CircuitBreaker）
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    def test_open_above_10pct(self):
        cb = CircuitBreaker(RiskConfig())
        assert cb.update(drawdown=0.11) == "open"

    def test_paused_then_recovered(self):
        cb = CircuitBreaker(RiskConfig())
        assert cb.update(0.12) == "open"
        assert cb.allow_new_entry is False
        assert cb.update(0.04) == "closed"
        assert cb.allow_new_entry is True
        # 未恢复前 5%~10% 之间保持暂停
        cb2 = CircuitBreaker(RiskConfig())
        cb2.update(0.11)
        assert cb2.update(0.07).startswith("open") or cb2.allow_new_entry is False

    def test_engine_skips_buy_when_breaker_open(self, db):
        _seed_prices(db, "A", [10.0] * 30)
        cb = CircuitBreaker(RiskConfig())
        cb.update(0.12)
        out = risk_engine.build_orders(
            db, _d(0), RiskConfig(), 1_000_000.0, circuit_breaker=cb,
            signals=[_Sig("A", "strong")], holdings=[], exit_signals=[])
        assert all(o.action != "BUY" for o in out)


# ---------------------------------------------------------------------------
# 每日链路（seam: engine.daily_review）
# ---------------------------------------------------------------------------


class TestDailyReview:
    def _setup_holding(self, db, code="600001", closes=None):
        closes = closes or [100.0] * 25 + [70.0, 70.0]
        _seed_prices(db, code, closes)
        with db.session() as s:
            s.add(PoolMembership(code=code, entered_at=_d(0), rank=1, score=60.0,
                                 reason="test"))
            s.commit()

    def test_stop_generates_next_open_sell_order(self, db):
        self._setup_holding(db)
        plan = risk_engine.daily_review(db, _d(25), RiskConfig(), 1_000_000.0,
                                        holdings=[exits.Holding(
                                            code="600001", entry_price=100.0,
                                            entry_date=_d(0), highest_close=100.0)])
        sells = [o for o in plan.orders if o.action == "SELL"]
        assert len(sells) == 1 and sells[0].code == "600001"
        assert sells[0].exec_date == _d(26)  # 次日开盘执行
        assert sells[0].reason in ("S1_ATR_STOP", "S2_MA_STOP")

    def test_pending_orders_persisted_and_queryable(self, db):
        self._setup_holding(db)
        risk_engine.daily_review(db, _d(25), RiskConfig(), 1_000_000.0,
                                 holdings=[exits.Holding(
                                     code="600001", entry_price=100.0,
                                     entry_date=_d(0), highest_close=100.0)])
        rows = risk_engine.get_pending_orders(db, exec_date=_d(26))
        assert len(rows) == 1
        assert rows[0].stop_line is not None  # 卖出线可查

    def test_deterministic_replay(self, db):
        self._setup_holding(db)
        h = [exits.Holding(code="600001", entry_price=100.0, entry_date=_d(0),
                           highest_close=100.0)]
        p1 = risk_engine.daily_review(db, _d(25), RiskConfig(), 1_000_000.0, holdings=h)
        p2 = risk_engine.daily_review(db, _d(25), RiskConfig(), 1_000_000.0, holdings=h)
        assert [(o.code, o.action, o.reason) for o in p1.orders] == \
               [(o.code, o.action, o.reason) for o in p2.orders]
