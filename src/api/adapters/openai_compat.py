"""OpenAI 兼容 API 适配器

覆盖: DeepSeek (V4 thinking mode), OpenAI, GLM
"""
from __future__ import annotations
from typing import Optional

from .base import BaseLLMAdapter
from ._utils import ensure_reasoning_content, _REASONER_PATTERNS, is_deepseek_v4_model


def _is_reasoner_model(model: str) -> bool:
    """判断模型是否需要 thinking 参数"""
    return any(p in model for p in _REASONER_PATTERNS) or is_deepseek_v4_model(model)


def _get_reasoning_effort() -> str:
    """读取当前推理等级（low/medium/high/max），异常回退 'max'。"""
    try:
        from ...config import REASONING_EFFORT as effort
    except Exception:
        return "max"
    return effort or "max"


class OpenAICompatAdapter(BaseLLMAdapter):
    """OpenAI 兼容 API 适配器"""

    provider_name = "openai_compat"

    def __init__(self, base_url: str = ""):
        self.base_url = base_url

    def is_reasoner_model(self, model: str) -> bool:
        """判断模型是否为推理模型（需要启用 thinking 参数）。"""
        return _is_reasoner_model(model)

    def prepare_messages(self, messages: list, model: str) -> list:
        """发送前预处理消息（tool 配对修复 + reasoning_content 等 provider 特定问题）"""
        messages = super().prepare_messages(messages, model)
        return ensure_reasoning_content(messages, model)

    def build_request_kwargs(
        self,
        messages: list,
        model: str,
        tools: Optional[list] = None,
        stream: bool = False,
        stream_options: Optional[dict] = None,
    ) -> dict:
        kwargs = self._build_base_kwargs(model, messages, tools, stream, stream_options)

        # 非 reasoner 子串匹配的 V4 模型需要 thinking 参数
        is_reasoner = _is_reasoner_model(model)
        if is_reasoner and not any(p in model for p in _REASONER_PATTERNS):
            kwargs["thinking"] = {
                "type": "enabled",
                "reasoning_effort": _get_reasoning_effort(),
            }

        return kwargs

    def parse_response(self, response: dict) -> dict:
        """解析非流式响应为统一格式"""
        return self._parse_openai_compat_response(response)

    # parse_stream_chunk 继承自 BaseLLMAdapter 的默认实现
    # （_parse_openai_stream_chunk），使用标准 choices[0].delta 格式解析。
    # DeepSeekAdapter 也共享同一实现，无需重复定义。
