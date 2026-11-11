"""交易日增量管线（issue #17，spec §3.3）。

- 交易日 17:00 触发：增量拉取当日行情入库（走 #15 数据源抽象 + #16 表结构）。
- 非交易日跳过不触发。
- 支持缺口补抓：昨日完整性校验发现缺口的股票，今日运行时回补。
- 每次运行后跑四条校验（#checks），结果写 data_check_log。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.data.checks import run_all_checks
from app.data.db import Database
from app.data.models import DailyBar
from app.data.trading_calendar import TradeCalendar


@dataclass
class IncrementResult:
    date: str
    skipped: bool = False
    bars: int = 0
    catchup_bars: int = 0
    alerts: dict = field(default_factory=dict)  # check_type -> level


class DailyIncrementPipeline:
    def __init__(
        self,
        db: Database,
        source,  # QuoteSource 协议实现
        calendar: TradeCalendar,
        codes: list[str],
        config=None,
    ):
        self.db = db
        self.source = source
        self.calendar = calendar
        self.codes = list(codes)
        if config is None:
            from app.common.config import DataConfig

            config = DataConfig()
        self.config = config

    def run(self, date: str, catchup_missing: dict[str, list[str]] | None = None) -> IncrementResult:
        """拉取 date 当日行情入库。catchup_missing: {code: [缺失日期]} 回补。"""
        res = IncrementResult(date=date)
        if not self.calendar.is_trading_day(date):
            res.skipped = True
            return res

        res.bars = self._upsert_bars(date)
        if catchup_missing:
            for code, dates in catchup_missing.items():
                for d in dates:
                    res.catchup_bars += self._upsert_one(code, d)

        res.alerts = run_all_checks(self.db, self.source, self.calendar, self.codes, date, self.config)
        return res

    # -- 内部 ------------------------------------------------------------

    def _upsert_bars(self, date: str) -> int:
        n = 0
        for code in self.codes:
            n += self._upsert_one(code, date)
        return n

    def _upsert_one(self, code: str, date: str) -> int:
        from app.data.db_models import DailyBar as ORMDailyBar

        with self.db.session() as s:
            exists = s.query(ORMDailyBar).filter_by(code=code, date=date).one_or_none()
            if exists is not None:
                return 0
            bars = self.source.get_daily_bars(code, date, date)
            target = next((b for b in bars if b.date == date), None)
            if target is None:
                return 0  # 停牌或数据源无数据，完整性校验留痕
            s.add(ORMDailyBar(**target.__dict__))
            s.commit()
            return 1


def to_orm(bar: DailyBar):
    from app.data.db_models import DailyBar as ORMDailyBar

    return ORMDailyBar(**bar.__dict__)
