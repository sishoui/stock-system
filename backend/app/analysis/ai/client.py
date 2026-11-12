"""LLM 客户端接口与真实实现（OpenAI 兼容协议，支持智谱/本地部署等）。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx


@dataclass(frozen=True)
class LLMResponse:
    content: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_cny: float = 0.0


class LLMClient(Protocol):
    """一次调用返回补全文本；真实实现由后续票接入。"""

    def complete(self, prompt: str, model: str = "") -> LLMResponse: ...


class OpenAIClient:
    """OpenAI 兼容接口客户端（spec §4.4 模型名仅为配置项）。

    base_url 例如 https://api.openai.com/v1 或 https://open.bigmodel.cn/api/paas/v4
    智谱 GLM 等兼容 OpenAI /v1/chat/completions 协议即可接入。
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-3.5-turbo",
        timeout: float = 60.0,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def complete(self, prompt: str, model: str = "") -> LLMResponse:
        model = model or self.model
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
        }
        with httpx.Client(timeout=self.timeout) as http:
            resp = http.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
        choice = data["choices"][0]
        content = choice["message"]["content"]
        usage = data.get("usage", {})
        return LLMResponse(
            content=content,
            model=data.get("model", model),
            tokens_in=int(usage.get("prompt_tokens", 0)),
            tokens_out=int(usage.get("completion_tokens", 0)),
            cost_cny=0.0,  # 成本由 CostTracker 按模型定价另行记账
        )
