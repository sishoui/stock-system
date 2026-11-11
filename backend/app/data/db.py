"""SQLite 数据库封装（spec §3.2）：SQLAlchemy 抽象方言 + WAL。"""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.data.db_models import Base


class Database:
    def __init__(self, url: str):
        from sqlalchemy import create_engine

        if url.startswith("sqlite:///") and not url.endswith(":memory:"):
            Path(url[len("sqlite:///"):]).parent.mkdir(parents=True, exist_ok=True)
        self.url = url
        self.engine: Engine = create_engine(url, future=True)
        self._session_factory = sessionmaker(bind=self.engine, future=True, expire_on_commit=False)
        self._register_pragmas(self.engine)

    @staticmethod
    def _register_pragmas(engine: Engine) -> None:
        @event.listens_for(engine, "connect")
        def _set_pragma(dbapi_conn, _record):  # pragma: no cover - driver specific
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)

    def session(self) -> Session:
        return self._session_factory()

    # -- 查询辅助 ----------------------------------------------------------

    def query_daily_bars(self, code: str, start: str, end: str) -> list:
        from app.data.models import DailyBar

        with self.session() as s:
            rows = (
                s.execute(
                    text(
                        "SELECT code, date, open, high, low, close, volume, amount"
                        " FROM daily_bar WHERE code=:c AND date BETWEEN :s AND :e ORDER BY date"
                    ),
                    {"c": code, "s": start, "e": end},
                )
                .mappings()
                .all()
            )
            return [
                DailyBar(
                    code=r["code"],
                    date=str(r["date"]),
                    open=r["open"],
                    high=r["high"],
                    low=r["low"],
                    close=r["close"],
                    volume=int(r["volume"]),
                    amount=r["amount"],
                )
                for r in rows
            ]

    def get_profiles(self) -> list:
        from app.data.db_models import StockProfile

        with self.session() as s:
            return list(s.query(StockProfile).all())


def default_db_url(env_var: str = "STOCK_DB_URL") -> str:
    return os.environ.get(env_var, "sqlite:///data/stock.db")
