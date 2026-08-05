"""Async 模型调用公开接口 — 与同步版 model.py 接口对等

提供 async call_model_async / call_model_sync_async，
内部使用 httpx.AsyncClient 实现非阻塞 I/O。
"""

from __future__ import annotations

import copy
import json
import logging
import time
from typing import Any

from .client_async import (
    chat_completions_async, chat_completions_async_anthropic,
)
from .tokens import estimate_tokens
from .interrupt_async import is_interrupted_async
from .stream.pipeline_async import stream_call_async
from .stats import (
    accumulate_usage, set_tool_parse_elapsed, set_stream_speed,
    add_token_size,
)
from ..config import MODEL
from ._retry import retry_api_call_async, retry_on_parse_failure_async
from ._adapter_manager import get_adapter

_logger = logging.getLogger(__name__)

# 向后兼容别名（供旧代码和测试引用）
_retry_api_call_async = retry_api_call_async
_retry_on_parse_failure_async = retry_on_parse_failure_async

# ── 公开接口 ────────────────────────────────────────────────

async def call_model_async(
    messages: list,
    model: str | None = None,
    tools: list | None = None,
    display=None,
    label: str | None = None,
    silent: bool = False,
    override_max_retries: int | None = None,
    fixed_delay_sec: float | None = None,
) -> tuple:
    """异步流式调用模型。

    返回 (reasoning_content, content, usage, tool_calls)。
    与同步 call_model 接口完全兼容。

    Args:
        override_max_retries: 覆盖最大重试次数（透传给重试层）。
            默认 None 使用全局 MAX_RETRIES；SubAgent 等快速失败场景传 1。
        fixed_delay_sec: 覆盖固定重试间隔（透传给重试层）。
            默认 None 使用全局 RETRY_BASE_SEC；SubAgent 场景传 0。
    """
    model = model or MODEL
    adapter = get_adapter(model)
    messages_copy = copy.deepcopy(messages)
    messages_copy = adapter.prepare_messages(messages_copy, model)
    is_reasoner = adapter.is_reasoner_model(model)
    return await retry_on_parse_failure_async(
        stream_call_async,
        silent=silent, display=display, label=label,
        api_args=(messages_copy, model, is_reasoner, tools, display, label, silent),
        override_max_retries=override_max_retries,
        fixed_delay_sec=fixed_delay_sec,
    )


async def call_model_sync_async(
    messages: list,
    model: str | None = None,
    tools: list | None = None,
    display=None,
    label: str | None = None,
    override_max_retries: int | None = None,
    fixed_delay_sec: float | None = None,
) -> tuple:
    """异步非流式模型调用（Agent 内部使用），无终端输出。

    返回 (reasoning_content, content, usage, tool_calls)。
    """
    model = model or MODEL
    adapter = get_adapter(model)
    messages_copy = copy.deepcopy(messages)
    messages_copy = adapter.prepare_messages(messages_copy, model)
    return await retry_on_parse_failure_async(
        _call_sync_async,
        silent=True, display=display, label=label,
        api_args=(messages_copy, model, tools, display, label),
        override_max_retries=override_max_retries,
        fixed_delay_sec=fixed_delay_sec,
    )


# ── 非流式调用实现（async） ─────────────────────────────────

async def _call_sync_async(
    messages: list,
    model: str,
    tools: list | None,
    display=None,
    label: str | None = None,
) -> tuple:
    """异步非流式模型调用。"""
    if await is_interrupted_async():
        return "", "(已中断)", {"input": 0, "output": 0}, []

    adapter = get_adapter(model)
    kwargs = adapter.build_request_kwargs(
        messages=messages,
        model=model,
        tools=tools,
    )

    start_time = time.time()
    if getattr(adapter, '_protocol', '') == 'anthropic':
        response = await chat_completions_async_anthropic(
            base_url=adapter._base_url, **kwargs)
    else:
        response = await chat_completions_async(**kwargs)
    api_duration = time.time() - start_time

    if await is_interrupted_async():
        return "", "(已中断)", {"input": 0, "output": 0}, []

    parsed = adapter.parse_response(response)
    content = parsed.get("content", "")
    reasoning_content = parsed.get("reasoning_content", "")
    usage = parsed.get("usage", {"input": 0, "output": 0})
    tool_calls = parsed.get("tool_calls", [])

    accumulate_usage(usage)
    add_token_size(usage.get("output", 0))

    if api_duration > 0 and usage["output"] > 0:
        speed = usage["output"] / api_duration
        usage["speed"] = speed
        set_stream_speed(speed)
    else:
        usage["speed"] = 0.0

    parse_elapsed = 0.0
    if tool_calls:
        parse_start = time.time()
        total_args = json.dumps([tc.get("arguments", {}) for tc in tool_calls])
        parse_elapsed = time.time() - parse_start
        parse_tokens = estimate_tokens(total_args)
        name_str = (
            ",".join(tc.get("name", "") for tc in tool_calls if tc.get("name"))
            or "工具"
        )
        set_tool_parse_elapsed(parse_elapsed)
        if display and label:
            try:
                display.update_parse_info(label, name_str, parse_tokens, parse_elapsed)
            except Exception:
                _logger.debug("更新并行显示解析信息失败", exc_info=True)

    usage["tool_parse_elapsed"] = parse_elapsed

    if reasoning_content and not content and not tool_calls:
        content = reasoning_content
    return reasoning_content, content, usage, tool_calls


# ── 同步兼容包装（持久化事件循环） ──────────────────────────
# 使用持久化事件循环替代 asyncio.run()，避免每次调用创建/销毁
# 新事件循环，从而防止 httpx.AsyncClient 因事件循环变换而触发
# "bound to a different event loop" 错误。
# 每个调用线程持有独立循环（threading.local），互不干扰。
# 事件循环管理逻辑已提取到 _model_loops.py。

from ._model_loops import _get_model_loop, cleanup_model_loops  # noqa: E402 — 事件循环管理


def call_model_sync(messages, model=None, tools=None, display=None, label=None):
    """同步兼容包装 — 在线程持久化事件循环中运行 async 调用。

    供 commands.py / context_manager.py 等尚未迁移到 async 的模块使用。
    """
    loop = _get_model_loop()
    return loop.run_until_complete(
        call_model_sync_async(messages, model, tools, display, label),
    )


def call_model(messages, model=None, tools=None, display=None, label=None, silent=False):
    """同步兼容包装 — 在线程持久化事件循环中运行 async 调用。

    供 SubAgent 等在线程中运行 sync 代码的模块使用。
    每个线程持有独立事件循环，互不干扰。
    """
    loop = _get_model_loop()
    return loop.run_until_complete(
        call_model_async(messages, model, tools, display, label, silent),
    )
