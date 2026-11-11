"""仓位结构（spec §7.3）：K=10、基准仓位=资金/K、强1.0×/中0.6×、单票≤15%、同行业≤30%。

达持仓上限：只替换最弱持仓（打分+AI 加权分最低）或放弃，不超限加仓。
"""
from __future__ import annotations

from dataclasses import dataclass

from app.common.config import RiskConfig


@dataclass
class SignalLike:
    code: str
    level: str  # strong / medium / weak
    industry: str
    score: float  # 打分 + AI 加权分


@dataclass
class HoldingLike:
    code: str
    industry: str
    weight: float
    score: float


@dataclass
class PlannedPosition:
    code: str
    industry: str
    weight: float
    action: str  # BUY / REPLACE（REPLACE 附 replace_code）


SCALE = {"strong": "position_scale_strong", "mid": "position_scale_mid"}


def base_weight(capital: float, config: RiskConfig) -> float:
    return 1.0 / config.max_positions


def allocate(capital: float, signals: list, holdings: list,
             config: RiskConfig) -> list[PlannedPosition]:
    """输入当日强/中信号与现有持仓权重，输出不超限的建仓计划。"""
    out: list[PlannedPosition] = []
    used = {getattr(h, "industry", ""): getattr(h, "weight", 0.0) for h in holdings}
    seats = config.max_positions - len(holdings)
    weakest = sorted(holdings, key=lambda h: getattr(h, "score", 0.0))

    for sig in signals:
        if sig.level not in SCALE:
            continue  # 弱信号不入
        w = base_weight(capital, config) * getattr(config, SCALE[sig.level])
        w = min(w, config.max_single_weight)  # 单票封顶

        # 同行业封顶：放不下则放弃该信号（不压其他票）
        if used.get(sig.industry, 0.0) + w > config.max_industry_weight + 1e-9:
            continue

        # 持仓上限：有空位直接开；无空位且新信号强于最弱持仓 → 替换
        if seats <= 0:
            if not weakest or sig.score <= weakest[0].score:
                continue  # 放弃
            victim = weakest.pop(0)
            out.append(PlannedPosition(sig.code, sig.industry, w, "REPLACE"))
            seats += 1  # 腾出一席
            used[sig.industry] = used.get(sig.industry, 0.0) + w
            seats -= 1
            continue

        out.append(PlannedPosition(sig.code, sig.industry, w, "BUY"))
        used[sig.industry] = used.get(sig.industry, 0.0) + w
        seats -= 1

    return out
