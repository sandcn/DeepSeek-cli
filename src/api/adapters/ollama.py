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


def _get_temperature() -> float | None:
    """读取请求应携带的温度（0.0~2.0）；思考模式开启时返回 None（不发送）。

    取值 = 当前生效模型档案的 ``temperature``（空则回退全局默认）。
    DeepSeek 思考模式不支持温度（设置不报错但被忽略），故思考模式开启时
    返回 ``None``；Ollama 本地模型 / 非 DeepSeek 接口通常不解析 ``thinking``
    参数，关闭思考模式（Ctrl+T）后温度即正常发送。
    """
    try:
        from ...config.model_profiles import request_temperature
        return request_temperature()
    except Exception:
        return 0.2


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
        # 温度：思考模式开启时 DeepSeek 会忽略该参数（官方文档「思考模式不
        # 支持 temperature」），此时不发送；关闭思考模式（Ctrl+T）后才携带。
        temperature = _get_temperature()
        extra_kwargs = {} if temperature is None else {"temperature": temperature}
        # ★ P0 修复: 参数顺序修正 — _build_base_kwargs 签名为 (model, messages, ...)
        kwargs = self._build_base_kwargs(
            model, messages, tools, stream, stream_options,
            extra_kwargs=extra_kwargs,
        )
        return kwargs

    def parse_response(self, response: dict) -> dict:
        """Ollama /v1/chat/completions 兼容格式解析"""
        return self._parse_openai_compat_response(response)
