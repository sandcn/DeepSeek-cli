"""LLM 适配器基类 — 提供默认实现骨架"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Optional


def _extract_cache_usage(raw_usage: dict) -> tuple[int, int]:
    """从 OpenAI 兼容格式的原始 usage 中提取缓存命中/未命中输入 token。

    兼容两种返回格式：
    - DeepSeek：``prompt_cache_hit_tokens`` / ``prompt_cache_miss_tokens``
    - OpenAI：``prompt_tokens_details.cached_tokens``（命中数，未命中 = 总输入 - 命中）

    Returns:
        (input_cache_hit, input_cache_miss) 均为 int；无法识别时返回 (0, 0)。
    """
    if not isinstance(raw_usage, dict):
        return 0, 0
    hit = raw_usage.get("prompt_cache_hit_tokens", 0) or 0
    miss = raw_usage.get("prompt_cache_miss_tokens", 0) or 0
    if hit or miss:
        return int(hit), int(miss)
    details = raw_usage.get("prompt_tokens_details") or {}
    if isinstance(details, dict):
        cached = details.get("cached_tokens", 0) or 0
        if cached:
            total = raw_usage.get("prompt_tokens", 0) or 0
            cached = int(cached)
            return cached, max(int(total) - cached, 0)
    return 0, 0


def _parse_openai_stream_chunk(chunk: dict) -> dict:
    """解析 OpenAI 兼容格式的流式 chunk 为统一增量格式

    这是 OpenAI / DeepSeek 等使用标准 choices[0].delta 格式的适配器
    共享的流式解析逻辑，提取到基类消除代码重复。

    chunk 格式:
    {
        "choices": [{"delta": {"content": "...", "reasoning_content": "...", "tool_calls": [...]}}],
        "usage": {...}  # 可选，通常在最后一个 chunk
    }
    """
    result: dict = {
        "content": "",
        "reasoning_content": "",
        "tool_calls": [],
        "usage": None,
    }

    # 处理 usage（通常在最后一个 chunk）
    chunk_usage = chunk.get("usage")
    if chunk_usage:
        cache_hit, cache_miss = _extract_cache_usage(chunk_usage)
        result["usage"] = {
            "input": chunk_usage.get("prompt_tokens", 0),
            "output": chunk_usage.get("completion_tokens", 0),
            "input_cache_hit": cache_hit,
            "input_cache_miss": cache_miss,
        }

    choices = chunk.get("choices")
    if not choices:
        return result

    try:
        first = choices[0]
    except (IndexError, TypeError):
        return result

    delta = first.get("delta", {}) if isinstance(first, dict) else {}
    if not delta:
        return result

    result["content"] = delta.get("content", "") or ""
    result["reasoning_content"] = delta.get("reasoning_content", "") or ""
    result["tool_calls"] = delta.get("tool_calls", []) or []

    return result


class BaseLLMAdapter(ABC):
    """LLM 适配器基类

    子类需实现:
    - build_request_kwargs()
    - parse_response()
    - parse_stream_chunk()  — 默认使用 _parse_openai_stream_chunk，非标准格式需重写
    """

    provider_name: str = "unknown"

    # ── 抽象方法 ───────────────────────────────────

    @abstractmethod
    def build_request_kwargs(
        self,
        messages: list,
        model: str,
        tools: Optional[list] = None,
        stream: bool = False,
        stream_options: Optional[dict] = None,
    ) -> dict:
        ...

    @abstractmethod
    def parse_response(self, response: dict) -> dict:
        ...

    def parse_stream_chunk(self, chunk: dict) -> dict:
        """解析流式 chunk 为统一增量格式

        默认使用 OpenAI 兼容格式（choices[0].delta），
        非标准格式的适配器（如 Anthropic）需重写此方法。
        """
        return _parse_openai_stream_chunk(chunk)

    def is_reasoner_model(self, model: str) -> bool:
        """判断模型是否为推理模型（如 DeepSeek Reasoner / Claude Thinking）。

        子类可重写此方法提供特定判断逻辑。
        """
        return False

    def prepare_messages(self, messages: list, model: str) -> list:
        """准备消息列表。默认不做处理直接透传，子类可重写。"""
        return messages

    # ── 共享响应解析 ────────────────────────────────────────

    def _parse_openai_compat_response(self, response: dict, *,
                                      preserve_raw_usage: bool = False) -> dict:
        """解析 OpenAI 兼容格式的非流式响应为统一 dict

        子类可通过参数控制差异：
        - preserve_raw_usage: 是否在 usage 中保留 _raw 字段（DeepSeek 调试用）
        """
        choices = response.get("choices", [])
        if not choices:
            return {
                "content": "",
                "reasoning_content": "",
                "usage": {"input": 0, "output": 0},
                "tool_calls": [],
            }

        first = choices[0]
        msg = first.get("message", {}) if isinstance(first, dict) else {}

        content = msg.get("content", "") or ""
        reasoning_content = msg.get("reasoning_content", "") or ""

        usage = {"input": 0, "output": 0, "input_cache_hit": 0, "input_cache_miss": 0}
        resp_usage = response.get("usage")
        if resp_usage:
            usage["input"] = resp_usage.get("prompt_tokens", 0)
            usage["output"] = resp_usage.get("completion_tokens", 0)
            cache_hit, cache_miss = _extract_cache_usage(resp_usage)
            usage["input_cache_hit"] = cache_hit
            usage["input_cache_miss"] = cache_miss
            if preserve_raw_usage:
                usage["_raw"] = resp_usage

        tool_calls = []
        raw_tool_calls = msg.get("tool_calls")
        if raw_tool_calls:
            from ..stream_parse import parse_raw_tool_calls_with_status
            parsed, _, _, failed_ids = parse_raw_tool_calls_with_status(raw_tool_calls)
            tool_calls = parsed
            if failed_ids:
                usage["_parse_failed_ids"] = failed_ids

        return {
            "content": content,
            "reasoning_content": reasoning_content,
            "usage": usage,
            "tool_calls": tool_calls,
        }

    def _build_base_kwargs(self, model: str, messages: list,
                           tools: list | None = None,
                           stream: bool = False,
                           stream_options: dict | None = None,
                           *,
                           extra_body: dict | None = None,
                           extra_kwargs: dict | None = None) -> dict:
        """构建 OpenAI 兼容格式的基础请求 kwargs。子类通过 extra_kwargs 注入差异。"""
        kwargs: dict[str, Any] = {"model": model, "messages": messages}
        if stream:
            kwargs["stream"] = True
        if tools:
            kwargs["tools"] = tools
        if stream_options:
            kwargs["stream_options"] = stream_options
        if extra_body:
            kwargs.setdefault("extra_body", {}).update(extra_body)
        if extra_kwargs:
            kwargs.update(extra_kwargs)
        return kwargs
