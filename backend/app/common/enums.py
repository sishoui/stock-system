"""代码级枚举值单一事实源（docs/agents/domain.md 约定）。

GLOSSARY 定义语义，本模块定义取值。跨层引用枚举值时 import 此处，不写字面量。
"""
from __future__ import annotations

# 入场信号级别（spec §6）：signals.py 产出 / risk sizing 消费 / API 暴露
SIGNAL_STRONG = "strong"
SIGNAL_MEDIUM = "medium"   # sizing.py 内部别名 "mid"，经 normalize_signal_level 归一
SIGNAL_WEAK = "weak"
SIGNAL_LEVELS = (SIGNAL_STRONG, SIGNAL_MEDIUM, SIGNAL_WEAK)

# 数据校验基线类型（spec §3.4，data_check_log.check_type）
CHECK_COMPLETENESS = "completeness"
CHECK_SUSPENSION = "suspension"
CHECK_CROSS = "cross"
CHECK_ABNORMAL = "abnormal"
CHECK_TYPES = (CHECK_COMPLETENESS, CHECK_SUSPENSION, CHECK_CROSS, CHECK_ABNORMAL)

# AI 三判断降级状态（spec §4，ai_assessment.status）
AI_ASSESSED = "assessed"
AI_PENDING = "pending"     # 「未评估」：降级/预算超限，权重为 0 不阻塞
AI_STATUSES = (AI_ASSESSED, AI_PENDING)


def normalize_signal_level(level: str) -> str:
    """兼容 sizing 历史别名 "mid" → "medium"；未知值原样返回由调用方报错。"""
    return SIGNAL_MEDIUM if level == "mid" else level
