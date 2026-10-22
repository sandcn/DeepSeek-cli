"""LLM 提供商协议 — 定义统一的模型调用接口"""
from __future__ import annotations
from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class LLMProtocol(Protocol):
    """LLM 适配器必须实现的协议"""

    def build_request_kwargs(
        self,
        messages: list,
        model: str,
        tools: Optional[list] = None,
        stream: bool = False,
        stream_options: Optional[dict] = None,
        max_tokens: Optional[int] = None,
    ) -> dict:
        """构建 API 请求参数（含 provider 特定的 thinking/参数处理）

        ``max_tokens``：单次响应的最大输出 token 数；``None`` = 不主动设置，
        ``<=0`` = 不限制（OpenAI 兼容 provider 不发送该字段，Anthropic 使用
        其最大输出常量）。
        """
        ...

    def parse_response(self, response: dict) -> dict:
        """解析 API 返回值为统一格式:
        {
            "content": str,
            "reasoning_content": str,
            "usage": {"input": int, "output": int},
            "tool_calls": [{"id": str, "name": str, "arguments": dict}],
        }
        """
        ...

    def parse_stream_chunk(self, chunk: dict) -> dict:
        """解析流式 chunk 为统一增量格式:
        {
            "content": str,          # delta content
            "reasoning_content": str, # delta reasoning
            "tool_calls": list,      # delta tool_calls
            "usage": dict | None,    # final usage when present
        }
        """
        ...

    provider_name: str
