"""DeepSeek API 专用适配器

封装 DeepSeek 特有的 API 差异：
- V4 thinking mode（reasoning_content 校验修复、thinking 参数注入）
- 模型分类（reasoner / V4 / classic）
- DeepSeek 特有的错误码处理（未来扩展）
- 权限/认证错误的中文化提示

设计原则：
- 与 OpenAI 兼容 API 共享 HTTP 传输层（client.py）
- 仅封装 DeepSeek 特有的请求构造/响应解析逻辑
- 出厂即正确：所有 DeepSeek 消息自动修复 reasoning_content
"""

from __future__ import annotations

import logging
from typing import Optional

from .base import BaseLLMAdapter
from ._utils import ensure_reasoning_content, _REASONER_PATTERNS, is_deepseek_v4_model

_logger = logging.getLogger(__name__)


# ── DeepSeek 模型系列常量 ──────────────────────────────────

# Classic 系列（无 thinking 参数）
_CLASSIC_MODELS: set[str] = {
    "deepseek-chat",
    "deepseek-coder",
}


# ═══════════════════════════════════════════════════════════════
# DeepSeekAdapter
# ═══════════════════════════════════════════════════════════════

def _get_reasoning_effort() -> str:
    """读取当前推理等级（low/medium/high/max），异常回退 'max'。

    延迟导入避免模块加载时的循环依赖；配置写入后缓存被清除，
    每次调用都能读到最新值。
    """
    try:
        from ...config import REASONING_EFFORT as effort
    except Exception:
        return "max"
    return effort or "max"


class DeepSeekAdapter(BaseLLMAdapter):
    """DeepSeek API 专用适配器

    当前支持的模型系列：
    - deepseek-v4-pro       — V4 旗舰版（thinking mode）
    - deepseek-v4-flash     — V4 快速版（thinking mode）
    - deepseek-reasoner     — 推理模型
    - deepseek-chat         — 经典对话模型
    - deepseek-coder        — 经典代码模型

    使用方式（由 model.py 自动路由）:
        adapter = DeepSeekAdapter()
        kwargs = adapter.build_request_kwargs(messages, model, tools)
        response = chat_completions(**kwargs)
        parsed = adapter.parse_response(response)
    """

    provider_name: str = "deepseek"

    # ── 模型分类 ──────────────────────────────────────────

    @staticmethod
    def is_reasoner_model(model: str) -> bool:
        """判断是否为推理模型（deepseek-reasoner 系列）

        推理模型需要特殊的 thinking 参数和 reasoning_content 处理。
        """
        return any(p in model for p in _REASONER_PATTERNS)

    @staticmethod
    def is_v4_model(model: str) -> bool:
        """判断是否为 V4 模型（deepseek-v4-* 系列）

        V4 模型使用 thinking mode，需要在 API 请求中注入 thinking 参数。
        """
        return is_deepseek_v4_model(model)

    # ── 消息预处理 ─────────────────────────────────────────

    def prepare_messages(self, messages: list, model: str) -> list:
        """发送前预处理消息（DeepSeek 特有的 reasoning_content 修复）"""
        return ensure_reasoning_content(messages, model)

    # ── 请求构造 ─────────────────────────────────────────

    def build_request_kwargs(
        self,
        messages: list,
        model: str,
        tools: Optional[list] = None,
        stream: bool = False,
        stream_options: Optional[dict] = None,
    ) -> dict:
        kwargs = self._build_base_kwargs(
            model, messages, tools, stream, stream_options,
            extra_kwargs={"temperature": 0.2},
        )

        # ── V4 thinking mode ────────────────────────────
        # deepseek-v4-* 系列（非 reasoner）需要注入 thinking 参数
        # 以启用推理能力的流式输出。
        # reasoner 模型已有内置推理能力，不需要额外参数。
        # reasoning_effort 可通过 /reasoning 命令调整（low/medium/high/max）。
        if self.is_v4_model(model) and not self.is_reasoner_model(model):
            kwargs["thinking"] = {
                "type": "enabled",
                "reasoning_effort": _get_reasoning_effort(),
            }

        return kwargs

    # ── 响应解析 ─────────────────────────────────────────

    def parse_response(self, response: dict) -> dict:
        """解析 DeepSeek API 非流式响应为统一格式

        与 OpenAI 兼容格式共享基类解析逻辑，
        DeepSeek 额外保留原始 usage 字段 + V4 thinking 转置。
        """
        result = self._parse_openai_compat_response(response, preserve_raw_usage=True)

        # DeepSeek V4 兼容：推理内容为空时可能将 content 填入 reasoning_content
        if result["reasoning_content"] and not result["content"] and not result["tool_calls"]:
            result["content"] = result["reasoning_content"]
            result["reasoning_content"] = ""

        return result

    # parse_stream_chunk 继承自 BaseLLMAdapter 的默认实现
    # （_parse_openai_stream_chunk），DeepSeek 的 SSE 格式与 OpenAI 兼容，
    # 使用标准 choices[0].delta 格式解析，无需重复定义。

    def __repr__(self) -> str:
        return "<DeepSeekAdapter>"
