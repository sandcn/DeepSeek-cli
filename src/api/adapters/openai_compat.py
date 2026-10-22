"""OpenAI 兼容 API 适配器

覆盖: DeepSeek (V4 thinking mode), OpenAI, GLM
"""
from __future__ import annotations
from typing import Optional

from .base import BaseLLMAdapter
from ._utils import ensure_reasoning_content, reasoner_patterns, is_deepseek_v4_model


def _is_reasoner_model(model: str) -> bool:
    """判断模型是否需要 thinking 参数"""
    return any(p in model for p in reasoner_patterns()) or is_deepseek_v4_model(model)


def _get_reasoning_effort() -> str:
    """读取当前生效模型的思考级别（low/medium/high/max），异常回退 'max'。

    取值 = 当前生效模型档案的 ``reasoning_effort``（空则回退全局默认）；
    ``/models`` 中可对每个模型独立设置。
    """
    try:
        from ...config.model_profiles import effective_reasoning_effort
        return effective_reasoning_effort()
    except Exception:
        return "max"


def _get_thinking_enabled() -> bool:
    """读取全局思考模式开关（Ctrl+T 切换，默认开启）；异常回退 True。"""
    try:
        from ...config.model_profiles import thinking_enabled
        return bool(thinking_enabled())
    except Exception:
        return True


def _get_temperature() -> float | None:
    """读取请求应携带的温度（0.0~2.0）；思考模式开启时返回 None（不发送）。

    取值 = 当前生效模型档案的 ``temperature``（空则回退全局默认）；
    ``/models`` 中可对每个模型独立设置。DeepSeek 思考模式不支持温度
    （设置不报错但被忽略），故思考模式开启时返回 ``None``。
    """
    try:
        from ...config.model_profiles import request_temperature
        return request_temperature()
    except Exception:
        return 0.2


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
        max_tokens: Optional[int] = None,
    ) -> dict:
        # 温度：思考模式开启时 DeepSeek 会忽略该参数（官方文档「思考模式不
        # 支持 temperature」），此时不发送；关闭思考模式（Ctrl+T）后才携带。
        temperature = _get_temperature()
        extra_kwargs = {} if temperature is None else {"temperature": temperature}
        kwargs = self._build_base_kwargs(
            model, messages, tools, stream, stream_options,
            max_tokens=max_tokens,
            extra_kwargs=extra_kwargs,
        )

        # 非 reasoner 子串匹配的 V4 模型需要 thinking 参数
        is_reasoner = _is_reasoner_model(model)
        if is_reasoner and not any(p in model for p in reasoner_patterns()):
            if _get_thinking_enabled():
                kwargs["thinking"] = {
                    "type": "enabled",
                    "reasoning_effort": _get_reasoning_effort(),
                }
            else:
                # 关闭思考模式（Ctrl+T）：DeepSeek V4 官方默认 enabled，
                # 必须显式传 disabled 才能真正关闭。
                kwargs["thinking"] = {"type": "disabled"}

        return kwargs

    def parse_response(self, response: dict) -> dict:
        """解析非流式响应为统一格式"""
        return self._parse_openai_compat_response(response)

    # parse_stream_chunk 继承自 BaseLLMAdapter 的默认实现
    # （_parse_openai_stream_chunk），使用标准 choices[0].delta 格式解析。
    # DeepSeekAdapter 也共享同一实现，无需重复定义。
