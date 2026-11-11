"""单只股票第一版输入组装（spec §4.2）。

结构化财务字段 + 管线校验标记 + 原始公告全文；
时效输入含公告日期/类型/交易日；可持续性输入含近 N 期财报趋势序列。
"""
from __future__ import annotations

from app.data.db import Database
from app.data.db_models import DataCheckLog, FinanceIndicator

from .inputs import AnnouncementProvider

TREND_PERIODS = 8  # 近 N 期财报趋势序列（N=8，约两年）


def build_inputs(
    db: Database, code: str, provider: AnnouncementProvider,
    *, trend_periods: int = TREND_PERIODS,
) -> dict:
    """组装三判断共享的输入对象（JSON 可序列化）。"""
    with db.session() as s:
        fin_rows = (
            s.query(FinanceIndicator)
            .filter(FinanceIndicator.code == code)
            .order_by(FinanceIndicator.report_period)
            .all()
        )
        finance = [
            {
                "report_period": r.report_period,
                "ann_date": r.ann_date,
                "roe": r.roe,
                "roe_ttm": r.roe_ttm,
                "revenue": r.revenue,
                "revenue_growth": r.revenue_growth,
                "net_profit": r.net_profit,
                "profit_growth": r.profit_growth,
                "gross_margin": r.gross_margin,
                "net_margin": r.net_margin,
                "debt_ratio": r.debt_ratio,
                "operating_cashflow": r.operating_cashflow,
            }
            for r in fin_rows
        ]
        check_rows = (
            s.query(DataCheckLog)
            .filter(DataCheckLog.stock_scope.contains(code))
            .order_by(DataCheckLog.check_date)
            .all()
        )
        check_flags = [
            {"type": c.check_type, "result": c.result, "detail": c.detail}
            for c in check_rows
        ]

    anns = provider.announcements(code)
    announcements = [
        {
            "ann_date": a.ann_date,
            "ann_type": a.ann_type,
            "title": a.title,
            "content": a.content,
        }
        for a in anns
    ]
    latest_ann = anns[-1] if anns else None
    timeliness = {
        "announcement_date": latest_ann.ann_date if latest_ann else None,
        "announcement_type": latest_ann.ann_type if latest_ann else None,
        "trading_day": provider.trading_day(),
    }
    trend = [
        {
            "report_period": f["report_period"],
            "revenue": f["revenue"],
            "net_profit": f["net_profit"],
            "gross_margin": f["gross_margin"],
            "operating_cashflow": f["operating_cashflow"],
        }
        for f in finance[-trend_periods:]
    ]
    return {
        "code": code,
        "finance": finance,
        "check_flags": check_flags,
        "announcements": announcements,
        "timeliness": timeliness,
        "trend": trend,
    }


def build_prompt(inputs: dict) -> str:
    """把输入渲染为提示词（JSON 块 + 契约说明）。"""
    import json

    contract = (
        "请阅读以下股票信息，一次输出三判断合一的 JSON 对象，"
        "字段结构固定为：\n"
        '{"authenticity": {"verdict": "pass|suspect|fail", "confidence": 0.0, "reasons": []},\n'
        ' "timeliness": {"valid_until": "YYYY-MM-DD", "decay_hint": "fast|medium|slow", "reasons": []},\n'
        ' "sustainability": {"score": 0-10 整数, "trend": "improving|stable|deteriorating", "key_risks": []},\n'
        ' "evidence": [{"source": "...", "quote": "..."}]}\n'
        "只输出 JSON，不要其他文字。\n\n"
    )
    return contract + "股票信息：\n" + json.dumps(inputs, ensure_ascii=False, indent=2)
