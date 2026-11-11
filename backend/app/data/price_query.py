"""复权价按需计算查询路径（spec §3.2：存原始不复权 + 因子，查询时复权）。"""
from __future__ import annotations

from sqlalchemy import text

from app.data.db import Database

_VALID = {"raw", "qfq", "hfq"}


class AdjustedPrice:
    __slots__ = ("code", "date", "open", "high", "low", "close", "volume", "amount", "factor")

    def __init__(self, code, date, o, h, l, c, v, a, factor):
        self.code, self.date = code, date
        self.open, self.high, self.low, self.close = o, h, l, c
        self.volume, self.amount, self.factor = v, a, factor


class PriceQuery:
    def __init__(self, db: Database):
        self.db = db

    def get_prices(
        self, code: str, start: str, end: str, adjust: str = "raw"
    ) -> list[AdjustedPrice]:
        """按 adjust 返回价格序列。

        - raw：原始不复权。
        - hfq（后复权）：价格 × 累计因子（相对区间首日基准，基准=首日因子）。
        - qfq（前复权）：价格 × 累计因子 / 最新因子（最新日 = 原始价）。
        """
        if adjust not in _VALID:
            raise ValueError(f"adjust 取值须为 {_VALID}，得到 {adjust!r}")

        with self.db.session() as s:
            bars = (
                s.execute(
                    text(
                        "SELECT code, date, open, high, low, close, volume, amount"
                        " FROM daily_bar WHERE code=:c AND date BETWEEN :s AND :e"
                        " ORDER BY date"
                    ),
                    {"c": code, "s": start, "e": end},
                )
                .mappings()
                .all()
            )
            factors = dict(
                s.execute(
                    text(
                        "SELECT date, factor FROM adjust_factor"
                        " WHERE code=:c AND date <= :e ORDER BY date"
                    ),
                    {"c": code, "e": end},
                ).all()
            )
        if not bars:
            return []

        # 累计因子：各 bar 的因子 = 不晚于当日最近一次因子值
        sorted_factor_dates = sorted(factors)
        cum: list[tuple[str, float]] = []
        last = 1.0
        fi = 0
        for b in bars:
            d = str(b["date"])
            while fi < len(sorted_factor_dates) and sorted_factor_dates[fi] <= d:
                last = factors[sorted_factor_dates[fi]]
                fi += 1
            cum.append((d, last))

        out: list[AdjustedPrice] = []
        if adjust == "raw":
            for b in bars:
                out.append(AdjustedPrice(b["code"], str(b["date"]), b["open"], b["high"],
                                         b["low"], b["close"], int(b["volume"]), b["amount"], 1.0))
            return out

        base = cum[0][1]  # hfq 基准
        newest = cum[-1][1]  # qfq 基准
        for b, (d, f) in zip(bars, cum):
            k = {"hfq": f / base, "qfq": f / newest}[adjust]
            out.append(AdjustedPrice(
                b["code"], d, b["open"] * k, b["high"] * k, b["low"] * k,
                b["close"] * k, int(b["volume"]), b["amount"], k,
            ))
        return out
