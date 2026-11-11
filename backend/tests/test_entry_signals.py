"""Issue #22：择时入场信号分级。stub 数据 + 临时 SQLite。

seam：
- signals.generate_entry_signals(db, date, config) —— 池内个股当日分级与理由
- signals.get_entry_signal(db, code, date) —— 单票信号状态查询
- 历史重放确定性：同日重放结果一致
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.data.db import Database
from app.data.db_models import AiAssessment
from app.strategy.pool_models import PoolBase, PoolMembership
from app.strategy.signals import (
    EntrySignalConfig,
    generate_entry_signals,
    get_entry_signal,
)

BASE = date(2024, 1, 1)


def _d(i: int) -> str:
    return (BASE + timedelta(days=i)).isoformat()


@pytest.fixture
def db(tmp_path) -> Database:
    d = Database(f"sqlite:///{tmp_path / 's22.db'}")
    d.create_all()
    PoolBase.metadata.create_all(d.engine)
    return d


def _seed_prices(db, code, closes, volumes, highs=None):
    """日线直接写 daily_bar / adjust_factor（factor=1，raw=qfq）。"""
    from sqlalchemy import text

    with db.session() as s:
        for i, (c, v) in enumerate(zip(closes, volumes)):
            h = highs[i] if highs else c
            s.execute(text(
                "INSERT INTO daily_bar(code,date,open,high,low,close,volume,amount)"
                " VALUES(:c,:d,:o,:h,:l,:cl,:v,:a)"
            ), {"c": code, "d": _d(i), "o": c, "h": h, "l": c, "cl": c,
                "v": v, "a": c * v})
        s.execute(text(
            "INSERT INTO adjust_factor(code,date,factor) VALUES(:c,:d,1.0)"
        ), {"c": code, "d": _d(0)})
        s.commit()


def _seed_pool(db, *codes):
    with db.session() as s:
        for c in codes:
            s.add(PoolMembership(code=c, entered_at=_d(0), rank=1, score=1.0,
                                 reason="test"))
        s.commit()


def _seed_ai(db, code, score, assessed_at=None):
    with db.session() as s:
        s.add(AiAssessment(code=code, assessed_at=assessed_at or _d(70),
                           authenticity="pass", authenticity_confidence=0.9,
                           sustainability_score=score, sustainability_trend="stable",
                           model_version="test-model", data_fingerprint="fp-" + code))
        s.commit()


def _flat_volume(n):
    return [100.0] * n


class TestCoreSignal:
    """seam: generate_entry_signals —— 核心趋势 + 量价确认。"""

    def test_cross_ma20_with_volume_is_strong_with_positive_ai(self, db):
        # 60 日横盘 10.0，信号日收 10.6 上穿 MA20，量 200 >= 1.5*100
        closes = [10.0] * 60 + [10.6]
        volumes = _flat_volume(60) + [200.0]
        highs = [10.0] * 60 + [10.6]
        _seed_prices(db, "600001.SH", closes, volumes, highs)
        _seed_pool(db, "600001.SH")
        _seed_ai(db, "600001.SH", 80.0)  # AI 正面

        result = generate_entry_signals(db, _d(60))
        sig = result["600001.SH"]
        assert sig.level == "strong"
        assert sig.trend is True
        assert sig.volume_confirm is True
        assert sig.ai == "positive"
        assert sig.reasons

    def test_trend_without_volume_is_medium(self, db):
        closes = [10.0] * 60 + [10.6]
        volumes = _flat_volume(60) + [100.0]  # 量不足
        _seed_prices(db, "600002.SH", closes, volumes)
        _seed_pool(db, "600002.SH")

        sig = generate_entry_signals(db, _d(60))["600002.SH"]
        assert sig.level == "medium"
        assert sig.trend is True
        assert sig.volume_confirm is False

    def test_no_trend_only_volume_is_weak(self, db):
        # 信号日收 9.5 跌破 MA20，量放大——仅单一信号
        closes = [10.0] * 60 + [9.5]
        volumes = _flat_volume(60) + [200.0]
        _seed_prices(db, "600003.SH", closes, volumes)
        _seed_pool(db, "600003.SH")

        sig = generate_entry_signals(db, _d(60))["600003.SH"]
        assert sig.level == "weak"
        assert sig.trend is False

    def test_no_trend_no_volume_no_signal_conditions_weak(self, db):
        closes = [10.0] * 60 + [9.5]
        volumes = _flat_volume(60) + [100.0]
        _seed_prices(db, "600004.SH", closes, volumes)
        _seed_pool(db, "600004.SH")

        sig = generate_entry_signals(db, _d(60))["600004.SH"]
        assert sig.level == "weak"


class TestAiCoupling:
    """AI 加减分：正面升级、中性不变、负面降级、不可用=中性不阻塞。"""

    def _setup(self, db, code):
        closes = [10.0] * 60 + [10.6]
        volumes = _flat_volume(60) + [200.0]
        _seed_prices(db, code, closes, volumes)
        _seed_pool(db, code)

    def test_ai_neutral_caps_at_medium(self, db):
        # 规格：强要求 AI 可持续性正面；中性 → 中
        self._setup(db, "600001.SH")
        _seed_ai(db, "600001.SH", 50.0)  # 中性
        sig = generate_entry_signals(db, _d(60))["600001.SH"]
        assert sig.level == "medium"
        assert sig.ai == "neutral"

    def test_ai_negative_downgrades(self, db):
        self._setup(db, "600002.SH")
        _seed_ai(db, "600002.SH", 10.0)  # 负面
        sig = generate_entry_signals(db, _d(60))["600002.SH"]
        assert sig.level == "medium"
        assert sig.ai == "negative"

    def test_ai_negative_downgrades_medium_to_weak(self, db):
        closes = [10.0] * 60 + [10.6]
        volumes = _flat_volume(60) + [100.0]  # 本为中
        _seed_prices(db, "600003.SH", closes, volumes)
        _seed_pool(db, "600003.SH")
        _seed_ai(db, "600003.SH", 10.0)
        sig = generate_entry_signals(db, _d(60))["600003.SH"]
        assert sig.level == "weak"

    def test_ai_unavailable_is_neutral_not_blocking(self, db):
        self._setup(db, "600004.SH")  # 无任何 AI 记录
        sig = generate_entry_signals(db, _d(60))["600004.SH"]
        assert sig.ai == "neutral"
        assert sig.level == "medium"  # 中性不变：趋势+量价但非正面 → 中
        # 不阻塞：信号仍然产生


class TestAggressiveBreakout:
    """可选激进信号：突破 60 日高，可配置开关。"""

    def _setup(self, db, code):
        # 缓跌后反弹但不触发 MA20 交叉前站稳条件：横盘后小幅上跳，量不足
        closes = [10.0] * 60 + [10.1]
        volumes = _flat_volume(60) + [100.0]
        _seed_prices(db, code, closes, volumes)
        _seed_pool(db, code)

    def test_breakout_counts_as_medium_even_without_volume(self, db):
        self._setup(db, "600001.SH")
        sig = generate_entry_signals(db, _d(60))["600001.SH"]
        assert sig.level == "medium"
        assert sig.breakout is True

    def test_breakout_disabled_no_trend_is_weak(self, db):
        # 禁用激进信号 + 无趋势（价格贴平 MA20）→ 弱
        closes = [10.0] * 60 + [9.5]
        volumes = _flat_volume(60) + [100.0]
        _seed_prices(db, "600002.SH", closes, volumes)
        _seed_pool(db, "600002.SH")
        cfg = EntrySignalConfig(aggressive_breakout=False)
        sig = generate_entry_signals(db, _d(60), cfg)["600002.SH"]
        assert sig.level == "weak"
        assert sig.breakout is False

    def test_breakout_true_but_disabled_not_reported(self, db):
        self._setup(db, "600003.SH")
        # 关闭开关时即使突破也不作为信号依据（仅趋势成立与否决定档位）
        cfg = EntrySignalConfig(aggressive_breakout=False)
        sig = generate_entry_signals(db, _d(60), cfg)["600003.SH"]
        assert sig.breakout is False


class TestPoolScopeAndReplay:
    """仅池内生效 + 历史重放确定性。"""

    def test_non_pool_stock_has_no_signal(self, db):
        closes = [10.0] * 60 + [10.6]
        _seed_prices(db, "600001.SH", closes, _flat_volume(61))
        _seed_pool(db, "600099.SH")  # 另一只在池
        result = generate_entry_signals(db, _d(60))
        assert "600001.SH" not in result
        assert "600099.SH" in result  # 数据不足时也给弱信号占位

    def test_insufficient_history_is_weak_with_reason(self, db):
        _seed_pool(db, "600001.SH")
        _seed_prices(db, "600001.SH", [10.0] * 10, _flat_volume(10))
        sig = generate_entry_signals(db, _d(9))["600001.SH"]
        assert sig.level == "weak"
        assert any("历史" in r for r in sig.reasons)

    def test_replay_same_day_is_deterministic(self, db):
        closes = [10.0] * 60 + [10.6]
        _seed_prices(db, "600001.SH", closes, _flat_volume(60) + [200.0])
        _seed_pool(db, "600001.SH")
        _seed_ai(db, "600001.SH", 80.0)

        a = get_entry_signal(db, "600001.SH", _d(60))
        b = get_entry_signal(db, "600001.SH", _d(60))
        assert (a.level, a.trend, a.volume_confirm, a.breakout, a.ai, a.reasons) == \
               (b.level, b.trend, b.volume_confirm, b.breakout, b.ai, b.reasons)

    def test_replay_earlier_day_differs(self, db):
        closes = [10.0] * 60 + [10.6]
        _seed_prices(db, "600001.SH", closes, _flat_volume(60) + [200.0])
        _seed_pool(db, "600001.SH")
        _seed_ai(db, "600001.SH", 80.0)

        early = get_entry_signal(db, "600001.SH", _d(40))
        late = get_entry_signal(db, "600001.SH", _d(60))
        assert early.level == "medium"    # 无趋势弱信号，但 AI 正面升一级
        assert late.level == "strong"     # 趋势+量价+AI 正面

    def test_uses_adjusted_prices_for_ma(self, db):
        """复权价参与均线：前一日除权（factor 减半）后 qfq 价连续，趋势不误判。"""
        from sqlalchemy import text
        closes = [20.0] * 60 + [10.6]  # 原始价：除权日腰斩
        _seed_prices(db, "600001.SH", closes, _flat_volume(60) + [200.0])
        with db.session() as s:
            s.execute(text(
                "INSERT INTO adjust_factor(code,date,factor) VALUES(:c,:d,2.0)"
            ), {"c": "600001.SH", "d": _d(60)})
            s.commit()
        _seed_pool(db, "600001.SH")
        sig = get_entry_signal(db, "600001.SH", _d(60))
        # qfq 下前 60 日 = 10.0，信号日 10.6 上穿 MA20 → 趋势成立
        assert sig.trend is True
