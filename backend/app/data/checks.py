"""四条数据校验基线（issue #17，spec §3.4），结果写 data_check_log。

1. completeness：应有行情的交易日/股票 vs 实际入库，缺失留痕，返回缺口供次日补抓。
2. suspension：volume=0/停牌标识识别停牌，不算缺口、不误报。
3. cross：抽样 50 只 + 全部自选股，对第二源收盘价，超阈值告警。
4. abnormal：涨跌幅超限（>±44% 新股沙盒外）/价格≤0/HLC 矛盾。
"""
from __future__ import annotations

from app.common.enums import CHECK_ABNORMAL, CHECK_COMPLETENESS, CHECK_CROSS, CHECK_SUSPENSION
from app.data.db import Database


def _log(db: Database, check_date: str, check_type: str, scope: str,
         result: str, level: str, detail: str) -> None:
    from app.data.db_models import DataCheckLog

    with db.session() as s:
        s.add(DataCheckLog(
            check_date=check_date, check_type=check_type, stock_scope=scope[:256],
            result=result, alert_level=level, detail=detail,
        ))
        s.commit()


# ---------------------------------------------------------------- 1 完整性

def completeness_check(
    db: Database, source, calendar, codes: list[str], date: str,
    watchlist: list[str] | None = None,
) -> list[str]:
    """返回当日缺行情的代码（停牌除外），并留痕。"""
    from app.data.db_models import DailyBar as ORMDailyBar

    all_codes = list(dict.fromkeys([*codes, *(watchlist or [])]))
    with db.session() as s:
        present = {
            c for (c,) in s.query(ORMDailyBar.code).filter(ORMDailyBar.date == date).all()
        }
        suspended = {
            c for (c,) in s.query(ORMDailyBar.code).filter(
                ORMDailyBar.date == date, ORMDailyBar.volume == 0
            ).all()
        }
    missing = [c for c in all_codes if c not in present and c not in suspended]
    if missing:
        _log(db, date, CHECK_COMPLETENESS, ",".join(missing), "warn", "warn",
             f"缺失 {len(missing)} 只: {','.join(missing)}")
    else:
        _log(db, date, CHECK_COMPLETENESS, "ALL", "ok", "info", "对账无缺口")
    return missing


# ---------------------------------------------------------------- 2 停牌

def suspension_check(db: Database, date: str) -> list[str]:
    """识别当日停牌股票（volume=0 有记录），留痕但不告警。"""
    from app.data.db_models import DailyBar as ORMDailyBar

    with db.session() as s:
        suspended = sorted(
            c for (c,) in s.query(ORMDailyBar.code).filter(
                ORMDailyBar.date == date, ORMDailyBar.volume == 0
            ).all()
        )
    if suspended:
        _log(db, date, CHECK_SUSPENSION, ",".join(suspended), "ok", "info",
             f"停牌 {len(suspended)} 只: {','.join(suspended)}")
    else:
        _log(db, date, CHECK_SUSPENSION, "ALL", "ok", "info", "无停牌")
    return suspended


# ---------------------------------------------------------------- 3 多源对账

def cross_check(
    db: Database, other_source, sample_codes: list[str], watchlist: list[str],
    date: str, tolerance: float = 0.005, sample_size: int = 50,
) -> list[str]:
    """抽样 sample_size 只 + 全部自选股，对第二源收盘价。返回超阈值代码。"""
    from app.data.db_models import DailyBar as ORMDailyBar

    targets = list(dict.fromkeys([*watchlist, *sample_codes]))[: max(len(watchlist), 0) + sample_size]
    # 自选股优先全部参与，其余从 sample_codes 抽样补足
    pool = [c for c in sample_codes if c not in watchlist]
    targets = watchlist + pool[:sample_size]

    with db.session() as s:
        rows = dict(
            s.query(ORMDailyBar.code, ORMDailyBar.close).filter(
                ORMDailyBar.date == date, ORMDailyBar.code.in_(targets)
            ).all()
        )

    mismatches: list[str] = []
    for code in targets:
        local = rows.get(code)
        if local is None:
            continue
        remote_bars = other_source.get_daily_bars(code, date, date)
        remote = next((b.close for b in remote_bars if b.date == date), None)
        if remote is None:
            continue
        if local and abs(remote - local) / local > tolerance:
            mismatches.append(code)

    if mismatches:
        _log(db, date, CHECK_CROSS, ",".join(mismatches), "warn", "warn",
             f"收盘价偏差超 {tolerance:.1%}: {','.join(mismatches)}")
    else:
        _log(db, date, CHECK_CROSS, "ALL", "ok", "info", f"对账 {len(targets)} 只无偏差")
    return mismatches


# ---------------------------------------------------------------- 4 异常值

def abnormal_check(db: Database, date: str, pct_limit: float = 0.44,
                   exempt_trading_days: int = 244) -> list[tuple[str, str]]:
    """返回 [(code, 原因)]。涨跌幅超限 / 价格≤0 / HLC 矛盾。留痕 error。

    新股豁免：上市（在库首个交易日）未满 exempt_trading_days（默认 244 ≈ 上市首年）
    的个股不触发涨跌幅告警——新股上市初期无涨跌幅限制属正常现象。
    """
    from sqlalchemy import text

    from app.data.db_models import DailyBar as ORMDailyBar

    issues: list[tuple[str, str]] = []
    with db.session() as s:
        rows = s.execute(
            text("SELECT code, open, high, low, close FROM daily_bar WHERE date=:d"),
            {"d": date},
        ).mappings().all()
        for r in rows:
            code = r["code"]
            if r["open"] <= 0 or r["close"] <= 0 or r["high"] <= 0 or r["low"] <= 0:
                issues.append((code, "price<=0"))
                continue
            if r["high"] < r["low"]:
                issues.append((code, "HLC矛盾"))
                continue
            if r["open"] > r["high"] or r["close"] > r["high"] or r["low"] > r["high"]:
                issues.append((code, "HLC矛盾"))
                continue
            prev = s.execute(
                text("SELECT close FROM daily_bar WHERE code=:c AND date<:d"
                     " ORDER BY date DESC LIMIT 1"),
                {"c": code, "d": date},
            ).scalar()
            if prev:
                pct = abs(r["close"] - prev) / prev
                if pct > pct_limit:
                    first_day = s.execute(
                        text("SELECT MIN(date) FROM daily_bar WHERE code=:c"),
                        {"c": code},
                    ).scalar()
                    if first_day:
                        n_days = s.execute(
                            text("SELECT COUNT(*) FROM daily_bar"
                                 " WHERE code=:c AND date<=:d"),
                            {"c": code, "d": date},
                        ).scalar() or 0
                        if n_days < exempt_trading_days:
                            continue  # 新股豁免
                    issues.append((code, f"涨跌幅 {pct:.1%} 超限"))

    if issues:
        scope = ",".join(c for c, _ in issues)
        _log(db, date, CHECK_ABNORMAL, scope, "error", "error",
             "; ".join(f"{c}:{r}" for c, r in issues))
    else:
        _log(db, date, CHECK_ABNORMAL, "ALL", "ok", "info", "无异常值")
    return issues


# ---------------------------------------------------------------- 全量入口

def run_all_checks(db, source, calendar, codes, date, config) -> dict[str, str]:
    """跑四条校验，返回 {check_type: alert_level}。"""
    watchlist = getattr(config, "watchlist", None) or []
    missing = completeness_check(db, source, calendar, codes, date, watchlist=watchlist)
    suspension_check(db, date)
    sample = codes[: config.reconcile_sample_size]
    cross_check(
        db, source, sample_codes=sample, watchlist=watchlist, date=date,
        tolerance=config.reconcile_price_tolerance, sample_size=config.reconcile_sample_size,
    )
    abnormal_check(db, date, pct_limit=config.check_anomaly_pct_limit,
                   exempt_trading_days=config.new_stock_exempt_trading_days)
    levels: dict[str, str] = {}
    with db.session() as s:
        from app.data.db_models import DataCheckLog

        for row in s.query(DataCheckLog).filter(DataCheckLog.check_date == date).all():
            levels.setdefault(row.check_type, row.alert_level)
    return levels
