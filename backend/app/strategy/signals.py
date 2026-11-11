"""择时入场信号分级（issue #22，spec §6）。

仅对股票池内个股生效：
- 核心信号（必须）：收盘价上穿 MA20，或已站稳 MA20 且 MA20 上拐。
- 量价确认（必须）：信号日成交量 ≥ 20 日均量的 1.5 倍。
- 可选激进信号：突破近 60 日高点，满足时即使量能不足也视为「中」。
- 分级：趋势+量价+AI 正面 → 强；其余有效组合 → 中；单一/无信号 → 弱。
- AI 加减分项：正面升级、中性不变、负面降一级，AI 不可用视为中性不阻塞。
- 无状态重算：任意历史交易日重放结果确定一致。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.analysis.ai.service import get_ai_factor
from app.data.price_query import PriceQuery
from app.strategy.pool_models import PoolBase, PoolMembership
from app.common.enums import SIGNAL_LEVELS, SIGNAL_MEDIUM, SIGNAL_STRONG, SIGNAL_WEAK

MA_WINDOW = 20
VOLUME_WINDOW = 20
VOLUME_CONFIRM_RATIO = 1.5
BREAKOUT_WINDOW = 60
MIN_HISTORY = MA_WINDOW + 1  # 计算信号至少需要的历史天数

# AI 可持续性分数 → 正面/中性/负面
AI_POSITIVE_MIN = 60.0
AI_NEGATIVE_MAX = 30.0


@dataclass
class EntrySignalConfig:
    """入场信号配置（可从 RiskConfig/主配置挂载）。"""

    aggressive_breakout: bool = True  # 可选激进信号开关


@dataclass
class EntrySignal:
    """单票单日入场信号状态。"""

    code: str
    date: str
    level: str  # strong / medium / weak
    trend: bool
    volume_confirm: bool
    breakout: bool
    ai: str  # positive / neutral / negative
    reasons: list[str] = field(default_factory=list)

    @property
    def entry_allowed(self) -> bool:
        """允许入场 = 强/中；弱仅观察不入场（spec §6）。"""
        return self.level in (SIGNAL_STRONG, SIGNAL_MEDIUM)


def _ensure_tables(db) -> None:
    PoolBase.metadata.create_all(db.engine)


def _pool_codes(db) -> list[str]:
    with db.session() as s:
        return [m.code for m in s.query(PoolMembership).all()]


def _ai_verdict(ai_factor: dict) -> str:
    """AI 可持续性 → positive/neutral/negative；不可用视为中性。"""
    if ai_factor.get("status") != "ok":
        return "neutral"
    score = ai_factor.get("sustainability_score")
    if score is None:
        return "neutral"
    if score >= AI_POSITIVE_MIN:
        return "positive"
    if score <= AI_NEGATIVE_MAX:
        return "negative"
    return "neutral"


def evaluate_entry_signal(
    db,
    code: str,
    trade_date: str,
    config: EntrySignalConfig | None = None,
) -> EntrySignal:
    """计算池内单票某交易日的入场信号（无状态，可确定性重放）。"""
    config = config or EntrySignalConfig()
    reasons: list[str] = []
    pq = PriceQuery(db)
    # 窗口：交易日往前 BREAKOUT_WINDOW 根（含当日），qfq 复权价保证均线连续
    bars = pq.get_prices(code, _shift_date(trade_date, -BREAKOUT_WINDOW), trade_date,
                         adjust="qfq")
    upto = [b for b in bars if b.date <= trade_date]
    if len(upto) < MIN_HISTORY:
        return EntrySignal(
            code=code, date=trade_date, level=SIGNAL_WEAK, trend=False,
            volume_confirm=False, breakout=False, ai="neutral",
            reasons=[f"历史数据不足（{len(upto)} < {MIN_HISTORY} 根），仅观察"],
        )

    closes = [b.close for b in upto]
    volumes = [float(b.volume) for b in upto]
    highs = [b.high for b in upto]
    close = closes[-1]
    ma20 = sum(closes[-MA_WINDOW:]) / MA_WINDOW
    prev_window = closes[-(MA_WINDOW + 1):-1]
    prev_ma20 = sum(prev_window) / MA_WINDOW

    # 核心趋势：上穿 MA20，或已站稳且 MA20 上拐
    above = close > ma20
    prev_above = prev_window[-1] > prev_ma20
    trend = (above and not prev_above) or (above and prev_above and ma20 > prev_ma20)
    if trend:
        if above and not prev_above:
            reasons.append("收盘价上穿 MA20")
        else:
            reasons.append("站稳 MA20 且 MA20 上拐")

    # 量价确认：信号日量 ≥ 1.5 × 20 日均量
    avg_vol = sum(volumes[-(VOLUME_WINDOW + 1):-1]) / VOLUME_WINDOW
    volume_confirm = volumes[-1] >= VOLUME_CONFIRM_RATIO * avg_vol
    if volume_confirm:
        reasons.append(f"成交量达 20 日均量 {volumes[-1] / avg_vol:.2f} 倍")

    # 可选激进信号：突破近 60 日高点（不含当日）
    prior_high = max(highs[:-1]) if len(highs) > 1 else 0.0
    breakout = config.aggressive_breakout and close > prior_high
    if breakout:
        reasons.append(f"突破 {BREAKOUT_WINDOW} 日高点 {prior_high:.2f}")

    ai = _ai_verdict(get_ai_factor(db, code))

    # 原始档：趋势+量价 → 强；仅趋势 → 中；其余 → 弱（激进信号保底中）
    if trend and volume_confirm:
        level = SIGNAL_STRONG
        reasons.append("趋势 + 量价确认同时满足")
    elif trend or breakout:
        level = SIGNAL_MEDIUM
    else:
        level = SIGNAL_WEAK

    # AI 加减分：正面升级、中性不变、负面降一级
    # 「强」要求 AI 可持续性正面：中性时强降为中（不变仅相对既有档位语义）
    if ai == "positive":
        if level != SIGNAL_STRONG:
            level = {SIGNAL_MEDIUM: SIGNAL_STRONG, SIGNAL_WEAK: SIGNAL_MEDIUM}[level]
        reasons.append("AI 可持续性正面（升一级）")
    elif ai == "negative":
        if level != SIGNAL_WEAK:
            level = {SIGNAL_STRONG: SIGNAL_MEDIUM, SIGNAL_MEDIUM: SIGNAL_WEAK}[level]
        reasons.append("AI 可持续性负面（降一级）")
    elif level == SIGNAL_STRONG:
        level = SIGNAL_MEDIUM
    return EntrySignal(code=code, date=trade_date, level=level, trend=trend,
                       volume_confirm=volume_confirm, breakout=breakout,
                       ai=ai, reasons=reasons)


def generate_entry_signals(
    db, trade_date: str, config: EntrySignalConfig | None = None
) -> dict[str, EntrySignal]:
    """对池内全部个股计算某交易日入场信号。返回 {code: EntrySignal}。"""
    _ensure_tables(db)
    return {
        code: evaluate_entry_signal(db, code, trade_date, config)
        for code in _pool_codes(db)
    }


def get_entry_signal(db, code: str, trade_date: str,
                     config: EntrySignalConfig | None = None) -> EntrySignal:
    """查询单票某交易日信号状态（强/中/弱 + 理由）。"""
    _ensure_tables(db)
    return evaluate_entry_signal(db, code, trade_date, config)


def _shift_date(iso_date: str, days: int) -> str:
    from datetime import date as _d, timedelta as _td

    return (_d.fromisoformat(iso_date) + _td(days=days)).isoformat()
