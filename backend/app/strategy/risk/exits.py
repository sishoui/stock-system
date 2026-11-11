"""S1 ATR 止损 / S2 MA20 逻辑止损（取先触发者）+ 移动止盈（3×ATR 与 8% 取宽者）。"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text

from app.common.config import RiskConfig
from app.data.db import Database

MIN_HISTORY = 15  # ATR(14) 至少需要 15 根 K 线


@dataclass
class Holding:
    """持仓快照（由组合状态层提供，本模块不持有数据库持仓表）。"""

    code: str
    entry_price: float
    entry_date: str
    highest_close: float  # 持有期最高收盘价


@dataclass
class ExitSignal:
    code: str
    date: str
    rule: str  # S1_ATR_STOP / S2_MA_STOP / TRAIL_TP
    stop_line: float  # 卖出线（触发价）
    distance_pct: float  # 收盘价距卖出线的百分比（负 = 已触发）
    atr: float | None = None


def _bars(db: Database, code: str, end: str, n: int) -> list[dict]:
    with db.session() as s:
        rows = s.execute(
            text(
                "SELECT date, high, low, close FROM daily_bar"
                " WHERE code=:c AND date<=:e ORDER BY date DESC LIMIT :n"
            ),
            {"c": code, "e": end, "n": n},
        ).mappings().all()
    return [dict(r) for r in reversed(rows)]


def atr14(db: Database, code: str, end: str, period: int = 14) -> float:
    """Wilder 平滑 ATR（回看 period+1 根）。"""
    bars = _bars(db, code, end, period + 1)
    if len(bars) < period + 1:
        raise ValueError(f"{code} 历史不足 {period + 1} 根，无法计算 ATR")
    trs = []
    prev_close = bars[0]["close"]
    for b in bars[1:]:
        trs.append(max(b["high"] - b["low"],
                       abs(b["high"] - prev_close),
                       abs(b["low"] - prev_close)))
    atr = sum(trs) / period  # 简单平均作种子
    # Wilder 平滑
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def ma20(db: Database, code: str, end: str, window: int = 20) -> float:
    bars = _bars(db, code, end, window)
    if len(bars) < window:
        raise ValueError(f"{code} 历史不足 {window} 根，无法计算 MA{window}")
    return sum(b["close"] for b in bars) / window


def exit_lines(db: Database, code: str, holding: Holding, date: str,
               config: RiskConfig) -> dict[str, float | None]:
    """三条卖出线，可查（验收：卖出线及距离可查）。"""
    atr = atr14(db, code, date, config.atr_period)
    s1 = holding.entry_price - config.atr_multiplier * atr
    try:
        s2 = ma20(db, code, date)
    except ValueError:
        s2 = None
    trail = max(holding.highest_close - config.trail_atr_mult * atr,
                holding.highest_close * (1 - config.trail_pct))
    return {"s1_stop": s1, "s2_stop": s2, "trail_tp": trail, "atr": atr}


def evaluate_exit(db: Database, holding: Holding, date: str,
                  config: RiskConfig) -> ExitSignal | None:
    """收盘后判定：任一卖出线被收盘价触发 → 次日开盘卖出。"""
    bars = _bars(db, holding.code, date, 1)
    if not bars:
        return None
    close = bars[0]["close"]
    lines = exit_lines(db, holding.code, holding, date, config)
    atr = lines["atr"]

    if close <= lines["s1_stop"]:
        return ExitSignal(holding.code, date, "S1_ATR_STOP", lines["s1_stop"],
                          (close / lines["s1_stop"] - 1) * 100, atr)
    if lines["s2_stop"] is not None and close < lines["s2_stop"]:
        return ExitSignal(holding.code, date, "S2_MA_STOP", lines["s2_stop"],
                          (close / lines["s2_stop"] - 1) * 100, atr)
    if close < lines["trail_tp"]:
        return ExitSignal(holding.code, date, "TRAIL_TP", lines["trail_tp"],
                          (close / lines["trail_tp"] - 1) * 100, atr)
    return None
