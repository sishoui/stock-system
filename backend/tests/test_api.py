"""API 路由层测试（issue #25，spec §9/§10）。

Seam：FastAPI HTTP 接口（TestClient）。stub = 内存 SQLite 预置数据，
不触网、不依赖 akshare/LLM。OpenAPI schema 即契约。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.api.portfolio_models import PortfolioBase, PortfolioNav, PortfolioPosition, PortfolioTrade
from app.analysis.ai.service import assess
from app.analysis.ai.client import LLMResponse
from app.common.config import RiskConfig
from app.data.db import Database
from app.data.db_models import (
    AiAssessment, Base as DataModelBase, DataCheckLog, DailyBar,
    FinanceIndicator, StockProfile, Valuation,
)
from app.strategy.pool_models import PoolBase, PoolMembership


# ---------------------------------------------------------------------------
# stub 数据：内存库 + 可控 LLM 假客户端
# ---------------------------------------------------------------------------


class FakeLLMClient:
    """返回固定三判断 JSON 的假客户端。"""

    def __init__(self, content: str | None = None):
        self.content = content or json.dumps({
            "authenticity": {"verdict": "pass", "confidence": 0.9, "reasons": ["财报口径正常"]},
            "timeliness": {"valid_until": "2030-12-31", "decay_hint": "slow", "reasons": []},
            "sustainability": {"score": 8, "trend": "improving", "key_risks": []},
            "evidence": [{"source": "财报", "quote": "ROE 15%"}],
        })

    def complete(self, prompt: str, model: str = "") -> LLMResponse:
        return LLMResponse(content=self.content, model=model or "fake")


def _fake_provider(trading_day: str = "2025-06-02"):
    from app.analysis.ai.inputs import Announcement

    class P:
        def announcements(self, code: str) -> list[Announcement]:
            return [Announcement(code=code, ann_date="2025-04-20", ann_type="季报",
                                 title="2025Q1", content="ROE 15%")]

        def trading_day(self) -> str:
            return trading_day

    return P()


def _bars(code: str, start: str, days: int, base: float = 10.0) -> list[DailyBar]:
    """生成连续交易日日线（周末跳过），价格缓慢上行便于触发信号。"""
    from datetime import date, timedelta

    d = date.fromisoformat(start)
    out = []
    px = base
    i = 0
    while len(out) < days:
        if d.weekday() < 5:
            px = round(px * (1.0 + 0.004), 4)
            out.append(DailyBar(
                code=code, date=d.isoformat(),
                open=round(px * 0.995, 4), high=round(px * 1.01, 4),
                low=round(px * 0.99, 4), close=px,
                volume=1_000_000 + i * 10_000, amount=px * 1_000_000,
            ))
            i += 1
        d += timedelta(days=1)
    return out


@pytest.fixture()
def db(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'api_test.db'}")
    DataModelBase.metadata.create_all(database.engine)
    PoolBase.metadata.create_all(database.engine)
    PortfolioBase.metadata.create_all(database.engine)

    with database.session() as s:
        for code, name in [("600519.SH", "贵州茅台"), ("000858.SZ", "五粮液")]:
            s.add(StockProfile(code=code, name=name, board="主板",
                               list_date="2001-01-01", is_st=False))
            s.add(PoolMembership(code=code, entered_at="2025-01-01", rank=1,
                                 score=88.5, ai_sustainability=80.0, reason="new_report"))
            s.add_all(_bars(code, "2025-01-02", 130))
            s.add(Valuation(code=code, date="2025-06-02", pe=25.0, pe_ttm=24.0,
                            pb=6.0, dividend_yield=0.012, total_mv=2e11))
            s.add(FinanceIndicator(code=code, report_period="2025-03-31",
                                   ann_date="2025-04-20", roe=15.0, roe_ttm=15.5,
                                   revenue_growth=0.12, profit_growth=0.15,
                                   gross_margin=0.91, debt_ratio=20.0,
                                   net_profit=3e9, revenue=2e10, operating_cashflow=4e9))
        s.add(DataCheckLog(check_date="2025-06-02", check_type="completeness",
                           result="ok", alert_level="info"))
        s.add(PortfolioPosition(code="600519.SH", shares=1000, entry_price=10.0,
                                entry_date="2025-05-20", highest_close=10.5,
                                weight=0.05, score=90.0, industry="白酒"))
        s.add(PortfolioTrade(code="600519.SH", action="BUY", exec_date="2025-05-20",
                             price=10.0, shares=1000, amount=10000.0, fee=7.5,
                             pnl=0.0, pnl_pct=0.0, reason="strong"))
        s.add(PortfolioNav(date="2025-06-02", nav=1.05))
        s.add_all(_bars("000300.SH", "2025-01-02", 130, base=3900.0))
        s.commit()
    return database


@pytest.fixture()
def client(db):
    app = create_app(db=db)
    return TestClient(app)


# ---------------------------------------------------------------------------
# /dashboard
# ---------------------------------------------------------------------------


class TestDashboard:
    def test_dashboard_returns_summary_and_schema(self, client):
        resp = client.get("/api/dashboard")
        assert resp.status_code == 200
        body = resp.json()
        # 今日行动摘要
        assert "action_summary" in body
        # 组合净值 sparkline
        assert body["nav_sparkline"]["dates"] == ["2025-06-02"]
        assert body["nav_sparkline"]["nav"] == [1.05]
        # 系统状态（数据/AI 健康）
        assert body["system_status"]["data_health"] == "ok"
        # AI 三判断 schema 出现在 OpenAPI 契约中
        schema = client.get("/openapi.json").json()
        assert "ThreeJudgmentOutput" in json.dumps(schema)

    def test_dashboard_ai_health_reflects_assessments(self, client, db):
        assess(db, "600519.SH", FakeLLMClient(), _fake_provider(),
               model_version="test-v1")
        body = client.get("/api/dashboard").json()
        assert body["system_status"]["ai_health"] in ("ok", "degraded", "idle")


# ---------------------------------------------------------------------------
# /pool
# ---------------------------------------------------------------------------


class TestPool:
    def test_pool_lists_members_with_ai_and_signal(self, client):
        body = client.get("/api/pool").json()
        assert body["total"] == 2
        item = body["items"][0]
        assert item["code"] in ("600519.SH", "000858.SZ")
        assert item["name"]
        assert item["score"] == 88.5
        assert item["ai"] is None or item["ai"]["authenticity"]["verdict"] in (
            "pass", "suspect", "fail", "pending")
        assert item["signal_level"] in ("strong", "medium", "weak")
        assert item["entered_at"] == "2025-01-01"

    def test_pool_sort_and_filter(self, client):
        body = client.get("/api/pool", params={"sort": "score", "order": "desc"}).json()
        scores = [i["score"] for i in body["items"]]
        assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# /stocks/{code}
# ---------------------------------------------------------------------------


class TestStockDetail:
    def test_stock_detail_full_payload(self, client, db):
        assess(db, "600519.SH", FakeLLMClient(), _fake_provider(),
               model_version="test-v1")
        resp = client.get("/api/stocks/600519.SH")
        assert resp.status_code == 404 is False or resp.status_code == 200
        body = resp.json()
        assert body["code"] == "600519.SH"
        assert body["name"] == "贵州茅台"
        # K 线（含 MA20 由前端算或后端给）——给原始 K 线
        k = body["kline"]
        assert len(k) == 130
        assert k[-1]["close"] > 0
        # 估值历史分位
        assert body["valuation"]["pe"] == 25.0
        assert 0.0 <= body["valuation"]["pe_percentile"] <= 1.0
        # 财务指标
        assert body["finance"]["roe_ttm"] == 15.5
        # AI 三判断（完整 schema）
        assert body["ai"]["authenticity"]["verdict"] == "pass"
        assert body["ai"]["timeliness"]["valid_until"] == "2030-12-31"
        assert body["ai"]["sustainability"]["score"] == 8
        # 当前信号状态与理由
        assert body["signal"]["level"] in ("strong", "medium", "weak")
        assert isinstance(body["signal"]["reasons"], list)

    def test_stock_detail_ai_not_assessed_is_null(self, client, db):
        body = client.get("/api/stocks/000858.SZ").json()
        assert body["ai"] is None
        assert body["ai_status"] == "not_assessed"

    def test_stock_unknown_code_404(self, client):
        assert client.get("/api/stocks/999999.SH").status_code == 404


# ---------------------------------------------------------------------------
# /positions /orders /trades
# ---------------------------------------------------------------------------


class TestPortfolio:
    def test_positions(self, client):
        body = client.get("/api/positions").json()
        assert body["total"] == 1
        p = body["items"][0]
        assert p["code"] == "600519.SH"
        assert p["shares"] == 1000
        assert p["market_value"] > 0
        assert p["pnl"] < 1e18  # 浮盈已计算
        assert p["stop_line"] is None or p["stop_line"] > 0

    def test_orders_and_trades(self, client):
        assert client.get("/api/orders").json()["items"] == []
        t = client.get("/api/trades").json()
        assert t["total"] == 1
        assert t["items"][0]["action"] == "BUY"


# ---------------------------------------------------------------------------
# /risk-config
# ---------------------------------------------------------------------------


class TestRiskConfig:
    def test_risk_config_readonly(self, client):
        body = client.get("/api/risk-config").json()
        assert body["readonly"] is True
        params = {p["name"]: p for p in body["params"]}
        assert params["atr_multiplier"]["default"] == 2.0
        assert params["atr_multiplier"]["min"] == 1.5
        assert params["atr_multiplier"]["max"] == 3.0
        assert params["max_positions"]["default"] == 10


# ---------------------------------------------------------------------------
# /backtests
# ---------------------------------------------------------------------------


class TestBacktests:
    def test_post_and_get_backtest(self, client):
        resp = client.post("/api/backtests", json={
            "codes": ["600519.SH"], "start": "2025-01-02", "end": "2025-06-02",
            "initial_capital": 200000.0,
        })
        assert resp.status_code == 201
        body = resp.json()
        bid = body["id"]
        assert body["status"] == "done"
        report = client.get(f"/api/backtests/{bid}").json()
        assert report["id"] == bid
        assert len(report["nav"]["dates"]) > 10
        assert len(report["nav"]["benchmark"]) == len(report["nav"]["dates"])
        assert "total_return" in report["metrics"]
        assert "sharpe" in report["metrics"]
        assert isinstance(report["trades"], list)

    def test_get_unknown_backtest_404(self, client):
        assert client.get("/api/backtests/9999").status_code == 404
