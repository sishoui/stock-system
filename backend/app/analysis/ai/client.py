"""LLM 客户端接口（stub 注入点，测试不真实调用 LLM）。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class LLMResponse:
    content: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_cny: float = 0.0


class LLMClient(Protocol):
    """一次调用返回补全文本；真实实现由后续票接入。"""

    def complete(self, prompt: str) -> LLMResponse: ...
