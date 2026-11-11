"""FastAPI 应用工厂（spec §9 路由域，OpenAPI 即契约）。

依赖注入：create_app(db=...) 由调用方提供 Database；
路由只读三判断因子（get_ai_factor），AI 挂掉不阻塞（视为未评估）。
"""
from __future__ import annotations

import json
from datetime import date

from fastapi import FastAPI, HTTPException, Query

from app.analysis.ai.service import get_ai_factor
from app.backtest.engine import BacktestEngine
from app.backtest.metrics import compute_metrics
from app.common.config import RiskConfig
from app.data.db import Database
from app.data.price_query import PriceQuery
from app.api import schemas as s
from app.api.portfolio_models import (
    BacktestRun, PortfolioBase, PortfolioNav, PortfolioPosition, PortfolioTrade,
)
from app.strategy.pool_models import PoolBase, PoolMembership
from app.strategy.risk.exits import Holding, exit_lines
from app.strategy.signals import EntrySignalConfig, get_entry_signal

RISK_CONFIG_PARAMS = [
    ("atr_multiplier", 2.0, 1.5, 3.0, "S1 止损 ATR 倍数"),
    ("atr_period", 14, 10, 20, "ATR 回看窗口"),
    ("trail_atr_mult", 3.0, 2.0, 4.0, "移动止盈 ATR 倍数"),
    ("trail_pct", 0.08, 0.05, 0.12, "移动止盈回撤比例"),
    ("max_positions", 10, 6, 15, "最大持仓数"),
    ("position_scale_strong", 1.0, 0.8, 1.2, "强信号仓位系数"),
    ("position_scale_mid", 0.6, 0.4, 0.8, "中信号仓位系数"),
    ("max_single_weight", 0.15, 0.10, 0.20, "单票仓位上限"),
    ("max_industry_weight", 0.30, 0.20, 0.40, "同行业仓位上限"),
    ("dd_circuit_break", 0.10, 0.08, 0.15, "熔断触发回撤"),
    ("dd_recover", 0.05, 0.03, 0.08, "熔断恢复线"),
]


def _ensure_tables(db: Database) -> None:
    """池/组合层建表；data 层表由数据管线负责。"""
    PoolBase.metadata.create_all(db.engine)
    PortfolioBase.metadata.create_all(db.engine)


def _name_of(db: Database, code: str) -> str | None:
    from app.data.db_models import StockProfile

    with db.session() as sess:
        row = sess.query(StockProfile).filter(StockProfile.code == code).first()
    return row.name if row else None


def _build_ai_output(db: Database, ai_factor: dict):
    """把 get_ai_factor 的扁平因子还原为三判断 schema（未评估 → None）。"""
    if ai_factor.get("status") != "ok":
        return None
    from app.analysis.ai.schemas import (
        AuthenticityJudgment, SustainabilityJudgment, ThreeJudgmentOutput,
        TimelinessJudgment,
    )
    from app.data.db_models import AiAssessment

    with db.session() as sess:
        row = (
            sess.query(AiAssessment)
            .filter(AiAssessment.code == ai_factor["code"])
            .order_by(AiAssessment.assessed_at.desc(), AiAssessment.id.desc())
            .first()
        )
    payload = json.loads(row.payload_json) if row else {}
    if not payload.get("authenticity"):
        return None
    try:
        from app.analysis.ai.schemas import ThreeJudgmentOutput as T

        return T.model_validate(payload)
    except Exception:
        return None


def _last_close(db: Database, code: str, before: str | None = None) -> float | None:
    from sqlalchemy import text

    with db.session() as sess:
        if before:
            v = sess.execute(
                text("SELECT close FROM daily_bar WHERE code=:c AND date<=:d"
                     " ORDER BY date DESC LIMIT 1"), {"c": code, "d": before}).scalar()
        else:
            v = sess.execute(
                text("SELECT close FROM daily_bar WHERE code=:c"
                     " ORDER BY date DESC LIMIT 1"), {"c": code}).scalar()
    return v


def create_app(db: Database, config: RiskConfig | None = None) -> FastAPI:
    config = config or RiskConfig()
    _ensure_tables(db)
    app = FastAPI(title="stock-system API", version="0.1.0")
    app.state.db = db
    app.state.config = config

    # -- /dashboard --------------------------------------------------------

    @app.get("/api/dashboard", response_model=s.DashboardResponse)
    def dashboard():
        with db.session() as sess:
            navs = sess.query(PortfolioNav).order_by(PortfolioNav.date).all()
            from app.data.db_models import DataCheckLog

            check = (
                sess.query(DataCheckLog)
                .order_by(DataCheckLog.check_date.desc(), DataCheckLog.id.desc())
                .first()
            )
            from app.api.portfolio_models import PortfolioTrade

            recent_trades = (
                sess.query(PortfolioTrade)
                .order_by(PortfolioTrade.exec_date.desc(), PortfolioTrade.id.desc())
                .limit(10).all()
            )
        data_health = check.result if check else "idle"
        # 行动摘要：池内强/中信号为可买，卖出指令（最近 SELL 成交近似）
        buy_candidates: list[str] = []
        watch: list[str] = []
        with db.session() as sess:
            members = sess.query(PoolMembership).all()
        for m in members:
            try:
                sig = get_entry_signal(db, m.code, _latest_date(db))
            except Exception:
                continue
            if sig.level == "strong":
                buy_candidates.append(m.code)
            elif sig.level == "medium":
                buy_candidates.append(m.code)
            elif sig.level == "weak":
                watch.append(m.code)
        sell_orders = [t.code for t in recent_trades if t.action == "SELL"][:5]
        ai_ok = any(get_ai_factor(db, m.code)["status"] == "ok" for m in members) \
            if members else False
        return s.DashboardResponse(
            action_summary=s.ActionSummary(
                date=_latest_date(db), buy_candidates=buy_candidates,
                sell_orders=sell_orders, watch=watch,
            ),
            nav_sparkline=s.NavSparkline(
                dates=[n.date for n in navs], nav=[n.nav for n in navs]),
            system_status=s.SystemStatus(
                data_health=data_health, ai_health="ok" if ai_ok else "idle",
                last_check_date=check.check_date if check else None,
            ),
        )

    def _latest_date(db: Database) -> str:
        from sqlalchemy import text

        with db.session() as sess:
            v = sess.execute(text("SELECT MAX(date) FROM daily_bar")).scalar()
        return str(v) if v else date.today().isoformat()

    # -- /pool ---------------------------------------------------------------

    @app.get("/api/pool", response_model=s.PoolResponse)
    def pool(sort: str = "rank", order: str = "asc"):
        with db.session() as sess:
            members = sess.query(PoolMembership).all()
        latest = _latest_date(db)
        items: list[s.PoolItem] = []
        for m in members:
            try:
                sig = get_entry_signal(db, m.code, latest)
                level, reasons = sig.level, sig.reasons
            except Exception:
                level, reasons = "weak", []
            af = get_ai_factor(db, m.code)
            items.append(s.PoolItem(
                code=m.code, name=_name_of(db, m.code) or m.code,
                entered_at=m.entered_at, rank=m.rank, score=m.score,
                ai=_build_ai_output(db, {**af, "code": m.code}),
                ai_status=af["status"], signal_level=level, signal_reasons=reasons,
            ))
        keymap = {
            "rank": lambda i: i.rank, "score": lambda i: i.score,
        }
        sort_key = keymap.get(sort, keymap["rank"])
        items.sort(key=lambda i: getattr(i, sort if sort in ("rank", "score") else "rank"),
                   reverse=(order == "desc"))
        return s.PoolResponse(total=len(items), items=items)

    # -- /stocks/{code} ------------------------------------------------------

    @app.get("/api/stocks/{code}", response_model=s.StockDetailResponse)
    def stock_detail(code: str, days: int = Query(130, ge=10, le=500)):
        profile = _name_of(db, code)
        if profile is None:
            raise HTTPException(404, f"unknown stock {code}")
        latest = _latest_date(db)
        pq = PriceQuery(db)
        start = _shift(latest, -days * 2)
        bars = pq.get_prices(code, start, latest, adjust="qfq")
        closes: list[float] = []
        kline: list[s.KLineBar] = []
        for b in bars[-days:]:
            closes.append(b.close)
            ma20 = sum(closes[-20:]) / len(closes[-20:]) if len(closes) >= 20 else None
            kline.append(s.KLineBar(date=b.date, open=b.open, high=b.high,
                                    low=b.low, close=b.close,
                                    volume=int(b.volume), ma20=ma20))
        from app.data.db_models import FinanceIndicator, Valuation

        with db.session() as sess:
            val = (
                sess.query(Valuation).filter(Valuation.code == code)
                .order_by(Valuation.date.desc()).first()
            )
            fin = (
                sess.query(FinanceIndicator).filter(FinanceIndicator.code == code)
                .order_by(FinanceIndicator.report_period.desc()).first()
            )
            all_pe = [
                v.pe for v in sess.query(Valuation)
                .filter(Valuation.code == code, Valuation.pe.isnot(None)).all()
            ]
            all_pb = [
                v.pb for v in sess.query(Valuation)
                .filter(Valuation.code == code, Valuation.pb.isnot(None)).all()
            ]

        def _pct(series: list[float], cur: float | None) -> float | None:
            if cur is None or not series:
                return None
            below = sum(1 for x in series if x <= cur)
            return below / len(series)

        af = get_ai_factor(db, code)
        try:
            sig = get_entry_signal(db, code, latest)
            signal = s.SignalInfo(level=sig.level, trend=sig.trend,
                                  volume_confirm=sig.volume_confirm,
                                  breakout=sig.breakout, ai=sig.ai,
                                  reasons=sig.reasons)
        except Exception:
            signal = s.SignalInfo(level="weak", trend=False, volume_confirm=False,
                                  breakout=False, ai="neutral", reasons=["数据不足"])
        return s.StockDetailResponse(
            code=code, name=profile, kline=kline,
            valuation=s.ValuationInfo(
                date=val.date if val else latest, pe=val.pe if val else None,
                pe_ttm=val.pe_ttm if val else None, pb=val.pb if val else None,
                pe_percentile=_pct(all_pe, val.pe if val else None),
                pb_percentile=_pct(all_pb, val.pb if val else None),
            ),
            finance=s.FinanceInfo(
                report_period=fin.report_period, roe=fin.roe, roe_ttm=fin.roe_ttm,
                revenue_growth=fin.revenue_growth, profit_growth=fin.profit_growth,
                gross_margin=fin.gross_margin, debt_ratio=fin.debt_ratio,
                net_profit=fin.net_profit, revenue=fin.revenue,
            ) if fin else None,
            ai=_build_ai_output(db, {**af, "code": code}),
            ai_status=af["status"], signal=signal,
        )

    def _shift(iso_date: str, days: int) -> str:
        from datetime import date as _d, timedelta as _td

        return (_d.fromisoformat(iso_date) + _td(days=days)).isoformat()

    # -- /positions /orders /trades ------------------------------------------

    @app.get("/api/positions", response_model=s.PositionsResponse)
    def positions():
        latest = _latest_date(db)
        with db.session() as sess:
            rows = sess.query(PortfolioPosition).all()
        items: list[s.PositionItem] = []
        for p in rows:
            close = _last_close(db, p.code, latest) or p.entry_price
            mv = p.shares * close
            cost = p.shares * p.entry_price
            try:
                lines = exit_lines(
                    db, p.code,
                    Holding(code=p.code, entry_price=p.entry_price,
                            entry_date=p.entry_date, highest_close=p.highest_close),
                    latest, config)
            except Exception:
                lines = {}
            items.append(s.PositionItem(
                code=p.code, name=_name_of(db, p.code), shares=p.shares,
                entry_price=p.entry_price, entry_date=p.entry_date,
                market_value=mv, pnl=mv - cost,
                pnl_pct=(mv / cost - 1.0) if cost else 0.0,
                weight=p.weight, stop_line=lines.get("s1_stop"),
                trail_line=lines.get("trail_tp"),
                stop_distance_pct=(
                    (close / lines["s1_stop"] - 1.0) * 100
                    if lines.get("s1_stop") else None),
            ))
        return s.PositionsResponse(total=len(items), items=items)

    @app.get("/api/orders", response_model=s.OrdersResponse)
    def orders():
        from app.strategy.risk.engine import PendingOrder, RiskBase

        RiskBase.metadata.create_all(db.engine)
        with db.session() as sess:
            rows = sess.query(PendingOrder).order_by(PendingOrder.exec_date).all()
        return s.OrdersResponse(total=len(rows), items=[
            s.OrderItem(code=r.code, action=r.action, exec_date=r.exec_date,
                        weight=r.weight, reason=r.reason, stop_line=r.stop_line)
            for r in rows
        ])

    @app.get("/api/trades", response_model=s.TradesResponse)
    def trades():
        with db.session() as sess:
            rows = (
                sess.query(PortfolioTrade)
                .order_by(PortfolioTrade.exec_date.desc(), PortfolioTrade.id.desc())
                .all()
            )
        return s.TradesResponse(total=len(rows), items=[
            s.TradeItem(code=r.code, action=r.action, exec_date=r.exec_date,
                        price=r.price, shares=r.shares, amount=r.amount,
                        fee=r.fee, pnl=r.pnl, pnl_pct=r.pnl_pct, reason=r.reason)
            for r in rows
        ])

    # -- /risk-config ---------------------------------------------------------

    @app.get("/api/risk-config", response_model=s.RiskConfigResponse)
    def risk_config():
        return s.RiskConfigResponse(params=[
            s.RiskConfigParam(name=n, default=d, min=mn, max=mx, description=desc)
            for n, d, mn, mx, desc in RISK_CONFIG_PARAMS
        ])

    # -- /backtests ------------------------------------------------------------

    @app.post("/api/backtests", response_model=s.BacktestSummary,
              status_code=201)
    def run_backtest(req: s.BacktestRequest):
        engine = BacktestEngine(db, config, initial_capital=req.initial_capital,
                                start=req.start, end=req.end)
        result = engine.run()
        now = _latest_date(db)
        with db.session() as sess:
            run = BacktestRun(
                status="done",
                params_json=req.model_dump_json(),
                result_json=json.dumps({
                    "dates": result.dates,
                    "nav": result.nav,
                    "benchmark_nav": result.benchmark_nav,
                    "metrics": result.metrics,
                    "trades": [t.__dict__ for t in result.trades],
                }, ensure_ascii=False),
                created_at=now,
            )
            sess.add(run)
            sess.commit()
            bid = run.id
        return s.BacktestSummary(id=bid, status="done", start=req.start,
                                 end=req.end, initial_capital=req.initial_capital)

    @app.get("/api/backtests/{backtest_id}", response_model=s.BacktestReportResponse)
    def backtest_report(backtest_id: int):
        with db.session() as sess:
            run = sess.query(BacktestRun).filter(BacktestRun.id == backtest_id).first()
        if run is None:
            raise HTTPException(404, f"backtest {backtest_id} not found")
        req = json.loads(run.params_json)
        result = json.loads(run.result_json)
        dates = result.get("dates", [])
        nav = result.get("nav", [])
        benchmark = result.get("benchmark_nav", [])
        metrics = result.get("metrics", {})
        if dates and not metrics:
            metrics = compute_metrics(nav, dates, benchmark, [], 0.0,
                                      req.get("initial_capital", 200_000.0))
        return s.BacktestReportResponse(
            id=run.id, status=run.status, start=req.get("start", ""),
            end=req.get("end", ""),
            initial_capital=req.get("initial_capital", 200_000.0),
            nav=s.NavSeries(dates=dates, nav=nav, benchmark=benchmark),
            metrics=metrics,
            trades=[s.TradeRecord(**{
                "code": t["code"], "entry_date": t["entry_date"],
                "entry_price": t["entry_price"], "exit_date": t.get("exit_date"),
                "exit_price": t.get("exit_price"), "shares": t["shares"],
                "pnl": t.get("pnl", 0.0), "pnl_pct": t.get("pnl_pct", 0.0),
                "reason": t.get("reason", ""),
            }) for t in result.get("trades", [])],
        )

    return app
