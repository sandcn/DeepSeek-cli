"""Anthropic API 适配器 — 消息格式转换

Anthropic 使用 /v1/messages 端点，消息格式与 OpenAI 不兼容。
此适配器在 OpenAI 格式和 Anthropic 格式之间做双向转换。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from .base import BaseLLMAdapter

_logger = logging.getLogger(__name__)


def _convert_image_url_to_anthropic(url: str) -> dict | None:
    """将 OpenAI image_url data URI 转换为 Anthropic image block。

    支持 ``data:image/<type>;base64,<data>`` 格式（read_image 工具输出）。
    无法解析时返回 None（调用方跳过该 block，不中断消息转换）。
    """
    if not url or not isinstance(url, str) or not url.startswith("data:"):
        return None
    try:
        header, _, data = url.partition(",")
        if not header.startswith("data:image/"):
            return None
        media_type = header[len("data:"):].split(";")[0].strip() or "image/png"
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": data},
        }
    except Exception:
        _logger.debug("image_url 转 Anthropic image block 失败", exc_info=True)
        return None


def _convert_tool_content_blocks(blocks: list) -> list:
    """将 OpenAI 兼容 content blocks 转为 Anthropic tool_result blocks。

    - text block → {"type": "text", "text": ...}
    - image_url block（data URI）→ {"type": "image", "source": {...}}
    - 其他已知 block 原样保留；无法转换的跳过。
    """
    converted: list = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            converted.append({"type": "text", "text": block.get("text", "")})
        elif btype == "image_url":
            url_container = block.get("image_url")
            url = url_container.get("url", "") if isinstance(url_container, dict) else ""
            img = _convert_image_url_to_anthropic(url)
            if img is not None:
                converted.append(img)
        elif btype == "image":
            # 已是 Anthropic image block，原样保留
            converted.append(block)
        elif btype in ("tool_result", "text_delta"):
            continue  # 嵌套结果/增量块无意义，跳过
        else:
            converted.append(block)
    return converted


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
            # max_tokens 为单次响应的最大输出 token 上限（Anthropic 必填）。
            # 4096 与全局 MAX_OUTPUT_CHARS≈3000 字符（约 750-1500 token）
            # 量级匹配；长输出任务（整文件生成）可在此提高，但受输出
            # 字符上限约束，保持当前值与 MAX_OUTPUT_CHARS 换算关系一致。
            "max_tokens": 4096,
        }
        # 大模型温度（从配置读取，Anthropic 官方支持范围 0.0~1.0，clamp 保证安全）
        try:
            from ...config import TEMPERATURE as _temperature
            kwargs["temperature"] = min(max(float(_temperature), 0.0), 1.0)
        except (ImportError, TypeError, ValueError):
            pass
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = self._convert_tools(tools)
        if stream:
            kwargs["stream"] = True
        return kwargs

    def _convert_messages(self, messages: list) -> tuple:
        """转换消息格式。返回 (system_text, anthropic_messages)

        连续 tool 消息合并：OpenAI 格式一次多工具调用产生 N 条连续
        role=tool 消息，逐条转换会产生 N 条连续 user 消息——Anthropic
        API 要求消息角色交替，连续 user 消息报 400（"roles must
        alternate"）。因此 tool 消息先累积为 tool_result blocks，
        在遇到下一条非 tool 消息（或末尾）时统一 flush 为单个
        user 消息（多个 tool_result blocks）。
        """
        system_parts: list[str] = []
        anthro_msgs: list[dict] = []
        pending_tool_results: list[dict] = []

        def _flush_tool_results() -> None:
            """将累积的连续 tool_result blocks 输出为单个 user 消息。"""
            nonlocal pending_tool_results
            if not pending_tool_results:
                return
            if anthro_msgs and anthro_msgs[-1]["role"] == "user":
                # 防御异常历史（tool 紧跟 user）：合并进最后一个 user 消息，
                # 避免连续 user 消息再次触发 API 400
                anthro_msgs[-1]["content"] = (
                    anthro_msgs[-1]["content"] + pending_tool_results
                )
            else:
                anthro_msgs.append({
                    "role": "user",
                    "content": pending_tool_results,
                })
            pending_tool_results = []

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "") or ""
            if role == "system":
                system_parts.append(content if isinstance(content, str) else str(content))
            elif role == "user":
                _flush_tool_results()
                if isinstance(content, list):
                    # 多模态用户消息（OpenAI 兼容 content blocks，如 image_url
                    # data URI）→ Anthropic content blocks（text/image）
                    anthro_msgs.append({
                        "role": "user",
                        "content": _convert_tool_content_blocks(content),
                    })
                else:
                    anthro_msgs.append({
                        "role": "user",
                        "content": [{"type": "text", "text": content}],
                    })
            elif role == "assistant":
                _flush_tool_results()
                tc = msg.get("tool_calls")
                if tc:
                    content_blocks: list[dict] = [
                        {"type": "text", "text": content or ""},
                    ]
                    for t in tc:
                        func = t.get("function") or {}
                        content_blocks.append({
                            "type": "tool_use",
                            "id": t.get("id", ""),
                            "name": func.get("name", ""),
                            "input": AnthropicAdapter._safe_json_loads(func.get("arguments", "{}")),
                        })
                    anthro_msgs.append({"role": "assistant", "content": content_blocks})
                else:
                    anthro_msgs.append({
                        "role": "assistant",
                        "content": [{"type": "text", "text": content}],
                    })
            elif role == "tool":
                content_value: Any = content
                if isinstance(content, list):
                    # 多模态 content blocks（OpenAI 兼容格式，如 image_url
                    # data URI）→ Anthropic tool_result blocks（text/image）
                    content_value = _convert_tool_content_blocks(content)
                pending_tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": msg.get("tool_call_id", ""),
                    "content": content_value,
                })
        _flush_tool_results()
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
        read = usage.get("cache_read_input_tokens", 0) or 0
        plain = usage.get("input_tokens", 0) or 0
        return {
            "content": content,
            "reasoning_content": "",
            "usage": {
                # 总输入 = 普通输入（含缓存创建）+ 缓存读取
                "input": plain + read,
                "output": usage.get("output_tokens", 0),
                "input_cache_hit": read,
                "input_cache_miss": plain,
            },
            "tool_calls": tool_calls,
        }

    def parse_stream_chunk(self, chunk: dict, state: dict | None = None) -> dict:
        """解析 Anthropic SSE 流式 chunk 为统一增量格式

        处理如下 Anthropic SSE 事件类型：
          - content_block_start：工具块开始，记录 id/name
          - content_block_delta：文本增量 (text_delta) 或工具参数增量 (input_json_delta)
          - content_block_stop：工具块结束，输出累积的完整 tool_call
          - message_delta：用量信息

        Args:
            chunk: Anthropic SSE 事件 dict。
            state: 每流独立的累积状态 dict（并发流安全）。调用方（如
                pipeline_async 的 _anthropic_to_unified）为每条流传入独立
                dict；None 时回退到实例级 _stream_tool_acc（向后兼容，
                仅限单流调用方使用——并发流须显式传 state，否则工具参数
                会在流间交叉污染）。

        注意：每次新流式调用开始时（message_start type），清空残留的
        累积状态，防止因前一次调用中途中断（content_block_stop
        未触发）导致工具参数残留污染本次调用。
        """
        # 每流独立累积状态：state 显式传入时用它（并发安全）；
        # 否则回退实例级字典（向后兼容）。
        acc = state if state is not None else self._stream_tool_acc

        chunk_type = chunk.get("type", "")
        if chunk_type == "message_start":
            if state is not None:
                state.clear()
            else:
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
                if key not in acc:
                    acc[key] = {"partial": "", "id": "", "name": ""}
                acc[key]["partial"] += partial_json

        elif chunk_type == "content_block_start":
            block = chunk.get("content_block", {})
            if block.get("type") == "tool_use":
                index = chunk.get("index", 0)
                key = f"tool_{index}"
                acc[key] = {
                    "partial": "",
                    "id": block.get("id", ""),
                    "name": block.get("name", ""),
                }
                # 若 input 已完整下发（含空对象 {}——空参数工具调用）直接输出。
                # ★ 用存在性判断 "input" in block 而非真值判断：空参数工具
                #   （input={}）下 block.get("input") 为 {}，真值判断会漏掉
                #   整个工具调用（消息序列缺 tool 消息 → 下一轮 API 400）。
                # 注：Anthropic 官方流式中 start 事件要么携带完整 input（此后
                # 无 input_json_delta，stop 时 partial 为空不会重复输出），
                # 要么不带 input 走 delta 累积路径——两条路径互斥，不会重复。
                if "input" in block and block.get("input") is not None:
                    result["tool_calls"] = [{
                        "id": block.get("id", ""),
                        "name": block.get("name", ""),
                        "arguments": block.get("input", {}),
                    }]

        elif chunk_type == "content_block_stop":
            index = chunk.get("index", 0)
            key = f"tool_{index}"
            acc_entry = acc.pop(key, None)
            if acc_entry and acc_entry["partial"]:
                try:
                    arguments = json.loads(acc_entry["partial"])
                except json.JSONDecodeError:
                    arguments = {"raw": acc_entry["partial"]}
                result["tool_calls"] = [{
                    "id": acc_entry["id"],
                    "name": acc_entry["name"],
                    "arguments": arguments,
                }]

        elif chunk_type == "message_delta":
            usage = chunk.get("usage", {})
            if usage:
                read = usage.get("cache_read_input_tokens", 0) or 0
                plain = usage.get("input_tokens", 0) or 0
                result["usage"] = {
                    "input": plain + read,
                    "output": usage.get("output_tokens", 0),
                    "input_cache_hit": read,
                    "input_cache_miss": plain,
                }

        return result
