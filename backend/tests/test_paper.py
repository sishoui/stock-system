"""Issue #26：模拟盘（stub/合成行情 + 临时 SQLite）。

seam（spec §8/§11/§12）：
- PaperTradingEngine.on_close(date)：收盘后决策 → 落 risk_pending_order（复用 #23 daily_review）
- PaperTradingEngine.on_open(date)：开盘按真实开盘价虚拟成交（复用 matching）→
  成交落 portfolio_trade、持仓落 portfolio_position、净值落 portfolio_nav（与回测一致口径）
- PaperTradingEngine.run(start, end)：连续两个交易日 决策→成交→净值 全链路
- paper_report(db)：从落表读回 净值曲线/胜率/回撤/持仓明细
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.common.config import RiskConfig
from app.data.db import Database

BASE = date(2024, 1, 2)


def _d(i: int) -> str:
    return (BASE + timedelta(days=i)).isoformat()


@pytest.fixture
def db(tmp_path) -> Database:
    d = Database(f"sqlite:///{tmp_path / 'paper.db'}")
    d.create_all()
    return d


def _seed_uptrend(db, codes=("600001.SH", "000300.SH"), days=40):
    """合成上行行情：确保产生入场信号且可成交。"""
    from sqlalchemy import text

    with db.session() as s:
        for code in codes:
            for i in range(days):
                dt = _d(i)
                c = 10.0 + i * 0.1
                o = c - 0.03
                s.execute(text(
                    "INSERT INTO daily_bar(code,date,open,high,low,close,volume,amount)"
                    " VALUES(:c,:d,:o,:h,:l,:cl,:v,:a)"
                ), {"c": code, "d": dt, "o": o, "h": c + 0.05, "l": o - 0.05,
                    "cl": c, "v": 1_000_000 if i > 20 else 500_000, "a": c * 1_000_000})
        s.commit()
    from app.strategy.pool_models import PoolMembership, PoolBase

    PoolBase.metadata.create_all(db.engine)
    with db.session() as s:
        s.add(PoolMembership(code="600001.SH", entered_at=_d(0), rank=1, score=90.0))
        s.commit()


def test_two_day_close_open_nav_persisted(db):
    """验收：连续两个交易日 决策 → 虚拟成交 → 净值落表。"""
    _seed_uptrend(db)
    from app.backtest.paper import PaperTradingEngine

    eng = PaperTradingEngine(db, RiskConfig(), initial_capital=200_000.0)
    # day1 收盘决策（应产生待执行 BUY 指令）
    plan = eng.on_close(_d(28))
    assert plan is not None
    from app.strategy.risk.engine import get_pending_orders

    orders = get_pending_orders(db, plan.next_exec_date)
    assert any(o.action == "BUY" for o in orders)
    # day2 开盘成交 + 收盘估值
    eng.on_open(_d(29))
    eng.on_close(_d(29))

    from app.api.portfolio_models import PortfolioNav, PortfolioTrade, PortfolioPosition

    with db.session() as s:
        navs = s.query(PortfolioNav).order_by(PortfolioNav.date).all()
        trades = s.query(PortfolioTrade).all()
        positions = s.query(PortfolioPosition).all()
    assert [n.date for n in navs] == [_d(28), _d(29)]
    assert navs[0].nav == pytest.approx(1.0)  # day1 仅估值未成交
    assert trades and trades[0].action == "BUY"
    assert trades[0].exec_date == _d(29)
    assert positions  # 持仓已落表


def test_report_shape(db):
    """paper_report 返回净值/指标/持仓，供前台复用回测展示口径。"""
    _seed_uptrend(db)
    from app.backtest.paper import PaperTradingEngine, paper_report

    eng = PaperTradingEngine(db, RiskConfig(), initial_capital=200_000.0)
    eng.on_close(_d(28))
    eng.on_open(_d(29))
    eng.on_close(_d(29))

    rep = paper_report(db)
    assert rep["initial_capital"] == 200_000.0
    assert [p["date"] for p in rep["nav"]] == [_d(28), _d(29)]
    assert "max_drawdown" in rep["metrics"]
    assert "win_rate" in rep["metrics"]
    assert isinstance(rep["positions"], list)


def test_scheduler_registers_paper_job():
    """APScheduler 编排每日模拟盘任务（spec §11）。"""
    from app.scheduler.paper import build_paper_scheduler

    sched = build_paper_scheduler(lambda d=None: None, lambda d=None: None, tz="Asia/Shanghai")
    jobs = {j.id: j for j in sched.get_jobs()}
    assert "paper_daily" in jobs


def test_api_paper_endpoint(db):
    """API /api/paper 返回模拟盘报告（复用 #25 展示口径）。"""
    from fastapi.testclient import TestClient

    from app.api.app import create_app
    from app.backtest.paper import PaperTradingEngine

    _seed_uptrend(db)
    PaperTradingEngine(db, RiskConfig()).run(_d(28), _d(29))
    client = TestClient(create_app(db))
    r = client.get("/api/paper")
    assert r.status_code == 200
    body = r.json()
    assert body["initial_capital"] == 200_000.0
    assert len(body["nav"]["dates"]) >= 1
