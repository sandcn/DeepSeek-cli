"""Anthropic API 适配器 — 消息格式转换

Anthropic 使用 /v1/messages 端点，消息格式与 OpenAI 不兼容。
此适配器在 OpenAI 格式和 Anthropic 格式之间做双向转换。
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from .base import BaseLLMAdapter

_logger = logging.getLogger(__name__)


class AnthropicAdapter(BaseLLMAdapter):
    """Anthropic API 适配器"""

    provider_name = "anthropic"
    _protocol: str = "anthropic"

    @staticmethod
    def _safe_json_loads(s: str | dict) -> dict:
        """安全解析 JSON 字符串，非合法 JSON 时返回空字典"""
        if isinstance(s, dict):
            return s
        try:
            return json.loads(s) if s else {}
        except json.JSONDecodeError:
            return {}

    def __init__(self, base_url: str = "https://api.anthropic.com/v1"):
        self._base_url = base_url.rstrip("/")
        # _stream_tool_acc 不再在 __init__ 中创建，改为在 parse_stream_chunk
        # 每次 message_start 时创建新实例，避免缓存适配器实例跨请求复用导致的残留污染。
        self._stream_tool_acc: dict = {}

    def is_reasoner_model(self, model: str) -> bool:
        return "thinking" in model

    def build_request_kwargs(
        self,
        messages: list,
        model: str,
        tools: Optional[list] = None,
        stream: bool = False,
        stream_options: Optional[dict] = None,
    ) -> dict:
        """将 OpenAI 格式消息转换为 Anthropic 格式

        每次新请求开始前清理流式累积状态，防止缓存适配器实例
        跨请求复用导致的 _stream_tool_acc 残留污染。

        通过 _protocol 标记路由到 chat_completions_async_anthropic，
        使用 x-api-key 头和 /v1/messages 端点。

        Args:
            stream_options: 被忽略。Anthropic API 通过 message_delta
                事件自动返回 usage，无需通过 stream_options 显式请求。
        """
        # ★ 每次新请求开始时创建新的流式累积状态实例，而非 clear() 复用，
        #    彻底避免缓存适配器实例跨请求复用导致的残留污染。
        self._stream_tool_acc = {}

        system, anthro_messages = self._convert_messages(messages)
        kwargs: dict = {
            "model": model,
            "messages": anthro_messages,
            "max_tokens": 4096,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = self._convert_tools(tools)
        if stream:
            kwargs["stream"] = True
        return kwargs

    def _convert_messages(self, messages: list) -> tuple:
        """转换消息格式。返回 (system_text, anthropic_messages)"""
        system_parts: list[str] = []
        anthro_msgs: list[dict] = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "") or ""
            if role == "system":
                system_parts.append(content if isinstance(content, str) else str(content))
            elif role == "user":
                anthro_msgs.append({
                    "role": "user",
                    "content": [{"type": "text", "text": content}],
                })
            elif role == "assistant":
                tc = msg.get("tool_calls")
                if tc:
                    content_blocks: list[dict] = [
                        {"type": "text", "text": content or ""},
                    ]
                    for t in tc:
                        content_blocks.append({
                            "type": "tool_use",
                            "id": t.get("id", ""),
                            "name": t.get("function", {}).get("name", ""),
                            "input": AnthropicAdapter._safe_json_loads(t.get("function", {}).get("arguments", "{}")),
                        })
                    anthro_msgs.append({"role": "assistant", "content": content_blocks})
                else:
                    anthro_msgs.append({
                        "role": "assistant",
                        "content": [{"type": "text", "text": content}],
                    })
            elif role == "tool":
                anthro_msgs.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": msg.get("tool_call_id", ""),
                        "content": content,
                    }],
                })
        return "\n".join(system_parts), anthro_msgs

    def _convert_tools(self, tools: list) -> list:
        """将 OpenAI 工具格式转换为 Anthropic 格式"""
        result = []
        for t in tools:
            func = t.get("function", t)
            result.append({
                "name": func.get("name", ""),
                "description": func.get("description", ""),
                "input_schema": func.get("parameters", {}),
            })
        return result

    def parse_response(self, response: dict) -> dict:
        """解析 Anthropic 非流式响应为统一格式"""
        content_blocks = response.get("content", [])
        content = ""
        tool_calls = []
        for block in content_blocks:
            if block.get("type") == "text":
                content += block.get("text", "")
            elif block.get("type") == "tool_use":
                tool_calls.append({
                    "id": block.get("id", ""),
                    "name": block.get("name", ""),
                    "arguments": block.get("input", {}),
                })
        usage = response.get("usage", {})
        return {
            "content": content,
            "reasoning_content": "",
            "usage": {
                "input": usage.get("input_tokens", 0),
                "output": usage.get("output_tokens", 0),
            },
            "tool_calls": tool_calls,
        }

    def parse_stream_chunk(self, chunk: dict) -> dict:
        """解析 Anthropic SSE 流式 chunk 为统一增量格式

        处理如下 Anthropic SSE 事件类型：
          - content_block_start：工具块开始，记录 id/name
          - content_block_delta：文本增量 (text_delta) 或工具参数增量 (input_json_delta)
          - content_block_stop：工具块结束，输出累积的完整 tool_call
          - message_delta：用量信息

        注意：每次新流式调用开始时（message_start type），清空残留的
        _stream_tool_acc，防止因前一次调用中途中断（content_block_stop
        未触发）导致工具参数残留污染本次调用。
        """
        # 在每次新流式调用开始时创建新的累积状态实例
        chunk_type = chunk.get("type", "")
        if chunk_type == "message_start":
            self._stream_tool_acc = {}

        result: dict = {
            "content": "",
            "reasoning_content": "",
            "tool_calls": [],
            "usage": None,
        }

        if chunk_type == "content_block_delta":
            delta = chunk.get("delta", {})
            if delta.get("type") == "text_delta":
                result["content"] = delta.get("text", "")
            elif delta.get("type") == "input_json_delta":
                # 累积增量 JSON 片段
                partial_json = delta.get("partial_json", "")
                index = chunk.get("index", 0)
                key = f"tool_{index}"
                if key not in self._stream_tool_acc:
                    self._stream_tool_acc[key] = {"partial": "", "id": "", "name": ""}
                self._stream_tool_acc[key]["partial"] += partial_json

        elif chunk_type == "content_block_start":
            block = chunk.get("content_block", {})
            if block.get("type") == "tool_use":
                index = chunk.get("index", 0)
                key = f"tool_{index}"
                self._stream_tool_acc[key] = {
                    "partial": "",
                    "id": block.get("id", ""),
                    "name": block.get("name", ""),
                }
                # 若 input 已完整下发（非流式场景），直接输出
                if block.get("input"):
                    result["tool_calls"] = [{
                        "id": block.get("id", ""),
                        "name": block.get("name", ""),
                        "arguments": block.get("input", {}),
                    }]

        elif chunk_type == "content_block_stop":
            index = chunk.get("index", 0)
            key = f"tool_{index}"
            acc = self._stream_tool_acc.pop(key, None)
            if acc and acc["partial"]:
                try:
                    arguments = json.loads(acc["partial"])
                except json.JSONDecodeError:
                    arguments = {"raw": acc["partial"]}
                result["tool_calls"] = [{
                    "id": acc["id"],
                    "name": acc["name"],
                    "arguments": arguments,
                }]

        elif chunk_type == "message_delta":
            usage = chunk.get("usage", {})
            if usage:
                result["usage"] = {
                    "input": usage.get("input_tokens", 0),
                    "output": usage.get("output_tokens", 0),
                }

        return result
