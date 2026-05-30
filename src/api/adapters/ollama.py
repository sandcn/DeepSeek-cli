"""Ollama 本地模型适配器

Ollama 提供 /v1/chat/completions 兼容端点，格式与 OpenAI 一致。

所有解析逻辑委托给 BaseLLMAdapter 的共享方法：
- build_request_kwargs → _build_base_kwargs
- parse_response → _parse_openai_compat_response
- parse_stream_chunk → 继承基类默认实现
"""
from __future__ import annotations

from typing import Optional

from .base import BaseLLMAdapter


class OllamaAdapter(BaseLLMAdapter):
    """Ollama 本地模型适配器"""

    provider_name = "ollama"

    def __init__(self, base_url: str = "http://localhost:11434"):
        self._base_url = base_url.rstrip("/")

    def build_request_kwargs(
        self,
        messages: list,
        model: str,
        tools: Optional[list] = None,
        stream: bool = False,
        stream_options: Optional[dict] = None,
    ) -> dict:
        # ★ P0 修复: 参数顺序修正 — _build_base_kwargs 签名为 (model, messages, ...)
        return self._build_base_kwargs(model, messages, tools, stream, stream_options)

    def parse_response(self, response: dict) -> dict:
        """Ollama /v1/chat/completions 兼容格式解析"""
        return self._parse_openai_compat_response(response)
