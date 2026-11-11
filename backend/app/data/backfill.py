"""一次性历史回补管线（issue #16，spec §3.2/§3.3）。

走 #15 的数据源抽象层（QuoteSource/FinanceSource/ProfileSource），
适配器可注入 mock/stub，不联网。
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass

from app.data.db import Database
from app.data.models import DailyBar
from app.data.sources import FinanceSource, ProfileSource, QuoteSource


class StubDataSource:
    """测试用 stub 数据源：确定性生成，不联网。

    - 日线：每月 20 个交易日，600519.SH 价格从 1700 缓涨；000001.SZ 从 11 缓涨。
    - 复权因子：首日 1.0，7 月起 1.05（模拟分红除权）。
    - 000001.SZ 标记 ST 留痕。
    """

    def __init__(self, codes: list[str] | None = None):
        self.codes = codes or ["600519.SH", "000001.SZ"]

    # -- QuoteSource -------------------------------------------------------

    def get_daily_bars(self, code: str, start: str, end: str) -> list[DailyBar]:
        bars: list[DailyBar] = []
        y0, m0 = int(start[:4]), int(start[5:7])
        y1, m1 = int(end[:4]), int(end[5:7])
        base = 1700.0 if code.startswith("6") else 11.0
        idx = 0
        for y in range(y0, y1 + 1):
            for m in range(1, 13):
                if (y, m) < (y0, m0) or (y, m) > (y1, m1):
                    continue
                for d in range(1, 21):  # 每月 20 根
                    date = f"{y:04d}-{m:02d}-{d:02d}"
                    close = base + idx * 0.5
                    bars.append(
                        DailyBar(
                            code=code,
                            date=date,
                            open=close - 0.5,
                            high=close + 1.0,
                            low=close - 1.0,
                            close=close,
                            volume=1_000_000 + idx * 1000,
                            amount=close * (1_000_000 + idx * 1000),
                        )
                    )
                    idx += 1
        return bars

    # -- FinanceSource -----------------------------------------------------

    def get_finance_indicators(self, code: str) -> list[dict]:
        out = []
        for i, period in enumerate(["2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31"]):
            out.append(
                {
                    "code": code,
                    "report_period": period,
                    "ann_date": period,
                    "roe": 15.0 + i,
                    "roe_ttm": 15.5 + i,
                    "revenue_growth": 10.0 + i,
                    "profit_growth": 12.0 + i,
                    "gross_margin": 30.0 + i,
                    "net_margin": 20.0 + i,
                    "debt_ratio": 40.0 + i,
                    "operating_cashflow": 1e8 + i * 1e7,
                    "net_profit": 5e8 + i * 1e7,
                    "revenue": 2e9 + i * 1e8,
                }
            )
        return out

    # -- ProfileSource -----------------------------------------------------

    def get_profile(self, code: str) -> dict:
        history = ["2023-01-01 listed"]
        is_st = not code.startswith("6")
        if is_st:
            history.append("2023-06-01 mark ST")
        return {
            "code": code,
            "name": "stub-600519" if code.startswith("6") else "stub-000001",
            "board": "主板",
            "list_date": "2001-08-27" if code.startswith("6") else "1991-04-03",
            "delist_date": None,
            "is_st": is_st,
            "status_history": "\n".join(history),
        }

    # -- 复权因子 ----------------------------------------------------------

    def get_adjust_factors(self, code: str, start: str, end: str) -> list[dict]:
        factors = []
        y0, m0 = int(start[:4]), int(start[5:7])
        y1, m1 = int(end[:4]), int(end[5:7])
        for y in range(y0, y1 + 1):
            for m in range(1, 13):
                if (y, m) < (y0, m0) or (y, m) > (y1, m1):
                    continue
                day = min(1, calendar.monthrange(y, m)[1])
                factors.append(
                    {"code": code, "date": f"{y:04d}-{m:02d}-{day:02d}",
                     "factor": 1.0 if m < 7 else 1.05}
                )
        return factors

    def get_valuations(self, code: str, start: str, end: str) -> list[dict]:
        vals = []
        for bar in self.get_daily_bars(code, start, end):
            vals.append(
                {
                    "code": code,
                    "date": bar.date,
                    "pe": 20.0,
                    "pe_ttm": 21.0,
                    "pb": 3.5,
                    "dividend_yield": 1.2,
                    "total_mv": 2e11,
                }
            )
        return vals


@dataclass
class BackfillResult:
    bars: int = 0
    factors: int = 0
    valuations: int = 0
    finance: int = 0
    profiles: int = 0


class BackfillPipeline:
    """对年份区间回补全市场：日线 + 复权因子 + 基础信息 + 财务 + 估值。

    source 需实现 QuoteSource/FinanceSource/ProfileSource，另加
    get_adjust_factors/get_valuations（真实适配器在 #15 基础上补齐；
    测试用 StubDataSource）。已有 (code, date) 记录自动跳过。
    """

    def __init__(self, db: Database, source, codes: list[str]):
        self.db = db
        self.source = source
        self.codes = codes

    def run(self, start_year: int, end_year: int) -> int:
        result = self.run_full(start_year, end_year)
        return result.bars

    def run_full(self, start_year: int, end_year: int) -> BackfillResult:
        start = f"{start_year}-01-01"
        end = f"{end_year}-12-31"
        res = BackfillResult()
        self.db.create_all()
        from app.data.db_models import (
            AdjustFactor,
            DailyBar,
            FinanceIndicator,
            StockProfile,
            Valuation,
        )

        with self.db.session() as s:
            existing = {
                (c, str(d)) for c, d in s.query(DailyBar.code, DailyBar.date).all()
            }
            for code in self.codes:
                # 日线
                new_bars = [
                    DailyBar(
                        code=b.code, date=b.date, open=b.open, high=b.high,
                        low=b.low, close=b.close, volume=b.volume, amount=b.amount,
                    )
                    for b in self.source.get_daily_bars(code, start, end)
                    if (b.code, b.date) not in existing
                ]
                if new_bars:
                    s.add_all(new_bars)
                res.bars += len(new_bars)
                # 复权因子（幂等：已有 (code,date) 跳过）
                existing_factors = {
                    (c, str(d)) for c, d in s.query(AdjustFactor.code, AdjustFactor.date).all()
                }
                new_factors = [
                    AdjustFactor(**f)
                    for f in self.source.get_adjust_factors(code, start, end)
                    if (f["code"], f["date"]) not in existing_factors
                ]
                s.add_all(new_factors)
                res.factors += len(new_factors)
                # 估值（幂等）
                existing_val = {
                    (c, str(d)) for c, d in s.query(Valuation.code, Valuation.date).all()
                }
                s.add_all(
                    Valuation(**v)
                    for v in self.source.get_valuations(code, start, end)
                    if (v["code"], v["date"]) not in existing_val
                )
                res.valuations += 1
                # 财务（upsert：已存在报告期跳过）
                existing_fi = {
                    (c, str(d)) for c, d in s.query(
                        FinanceIndicator.code, FinanceIndicator.report_period
                    ).all()
                }
                s.add_all(
                    FinanceIndicator(**f)
                    for f in self.source.get_finance_indicators(code)
                    if (f["code"], f["report_period"]) not in existing_fi
                )
                res.finance += 1
                # 基础信息（ST/退市留痕；已存在则追加历史）
                p = self.source.get_profile(code)
                row = s.query(StockProfile).filter_by(code=code).one_or_none()
                if row is None:
                    s.add(StockProfile(**p))
                    res.profiles += 1
                else:
                    for line in p["status_history"].splitlines():
                        if line and line not in row.status_history:
                            row.status_history = (row.status_history + "\n" + line).strip()
                    row.is_st = p["is_st"]
            s.commit()
        return res
