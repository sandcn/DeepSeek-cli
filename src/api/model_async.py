"""Async 模型调用公开接口 — 与同步版 model.py 接口对等

提供 async call_model_async / call_model_sync_async，
内部使用 httpx.AsyncClient 实现非阻塞 I/O。
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import random
import threading
import time
from typing import Any

import httpx

from .client_async import chat_completions_async, RateLimitError, APIError, _CONNECTION_ERRORS
from .tokens import estimate_tokens
from .interrupt_async import is_interrupted_async, wait_for_interrupt_async, request_interrupt_async
from .stream.pipeline_async import stream_call_async
from .stats import (
    accumulate_usage, set_tool_parse_elapsed, set_stream_speed,
    add_token_size,
)
from .adapters import OpenAICompatAdapter, DeepSeekAdapter
from ..config import MODEL, MAX_RETRIES, RETRY_BASE_SEC
from ..ui._lock import locked_print

_logger = logging.getLogger(__name__)

RETRY_EXPONENT_BASE = 2

# 适配器缓存（线程安全）
_adapter_cache: dict[str, Any] = {}
_adapter_cache_lock = threading.Lock()


def _get_adapter(model: str) -> Any:
    """根据模型名获取适配器（带缓存，线程安全）。"""
    with _adapter_cache_lock:
        if model in _adapter_cache:
            return _adapter_cache[model]
        
        # 按前缀匹配，长前缀优先避免歧义
        model_lower = model.lower()
        if model_lower.startswith("deepseek"):
            _adapter_cache[model] = DeepSeekAdapter()
        elif model_lower.startswith("anthropic") or "claude" in model_lower:
            from .adapters.anthropic import AnthropicAdapter
            _adapter_cache[model] = AnthropicAdapter()
        elif model_lower.startswith("ollama"):
            from .adapters.ollama import OllamaAdapter
            _adapter_cache[model] = OllamaAdapter()
        else:
            _adapter_cache[model] = OpenAICompatAdapter()
    return _adapter_cache[model]


# ── 重试逻辑（async） ──────────────────────────────────────

async def _retry_api_call_async(
    api_func,
    *,
    silent: bool = False,
    display=None,
    label: str | None = None,
    api_args: tuple = (),
):
    """异步版重试逻辑。

    Args:
        api_func: 要调用的异步模型函数
        api_args: 传给 api_func 的位置参数元组
        silent/display/label: 重试报告控制参数
    """
    async def _report(msg: str):
        if display is not None and label:
            display.update_model_phase(label, "error", msg)
        elif not silent:
            from ..core.constants import YELLOW, RESET
            locked_print(f"\n{YELLOW}{msg}{RESET}", flush=True)
        # silent=True 且无 display 时：不输出

    empty = ("", "(已中断，无内容)", {"input": 0, "output": 0}, [])

    for attempt in range(1, MAX_RETRIES + 1):
        if await is_interrupted_async():
            await _report("已中断生成（保留部分内容）")
            return empty
        try:
            return await api_func(*api_args)
        except KeyboardInterrupt:
            await _report("已中断生成（保留部分内容）")
            request_interrupt_async()
            return empty
        except _CONNECTION_ERRORS as e:
            _logger.warning(
                "连接错误 (尝试 %d/%d): %s", attempt, MAX_RETRIES, e, exc_info=True,
            )
            if attempt < MAX_RETRIES:
                wait = RETRY_BASE_SEC * (RETRY_EXPONENT_BASE ** (attempt - 1))
                wait *= random.uniform(0.8, 1.5)
                await _report(f"连接错误 (第{attempt}次): {e}，{wait:.1f}秒后重试...")
                if await wait_for_interrupt_async(wait):
                    return ("", "(已中断)", {"input": 0, "output": 0}, [])
            else:
                await _report(f"连接错误 (已重试{MAX_RETRIES}次): {e}")
                return ("", f"连接错误: {str(e)}", {"input": 0, "output": 0}, [])
        except (
            httpx.HTTPStatusError, httpx.RequestError,
            json.JSONDecodeError, ConnectionError, TimeoutError,
            RateLimitError,
        ) as e:
            _logger.warning(
                "API 调用失败 (尝试 %d/%d): %s", attempt, MAX_RETRIES, e, exc_info=True,
            )
            if attempt < MAX_RETRIES:
                wait = RETRY_BASE_SEC * (RETRY_EXPONENT_BASE ** (attempt - 1))
                if isinstance(e, RateLimitError):
                    wait = max(wait, 10 * attempt)
                    _logger.info("速率限制错误，等待 %d 秒", wait)
                wait *= random.uniform(0.8, 1.5)
                await _report(f"API 调用失败 (第{attempt}次): {e}，{wait:.1f}秒后重试...")
                if await wait_for_interrupt_async(wait):
                    return ("", "(已中断)", {"input": 0, "output": 0}, [])
            else:
                await _report(f"API 调用失败 (已重试{MAX_RETRIES}次): {e}")
                return ("", f"抱歉，API 调用出错: {str(e)}", {"input": 0, "output": 0}, [])
        except asyncio.CancelledError:
            _logger.warning(
                "API 调用被取消 (尝试 %d/%d)", attempt, MAX_RETRIES, exc_info=False,
            )
            await _report("已中断生成（保留部分内容）")
            return empty
        except Exception as e:
            _logger.warning("API 调用出现非重试异常: %s", e, exc_info=True)
            return ("", f"模型调用出错: {e}", {"input": 0, "output": 0, "error": str(type(e).__name__)}, [])

    return ("", "", {"input": 0, "output": 0}, [])


# ── 解析重试逻辑 ────────────────────────────────────────────
# 解析重试与 API 重试是两个独立层级：
#   _retry_api_call_async    → API 级重试（网络错误、速率限制等）
#   _retry_on_parse_failure_async → 解析级重试（JSON 解析失败时重新生成）
# 解析重试包装在 API 重试外层，不修改 _retry_api_call_async 内部逻辑。

_MAX_PARSE_RETRIES = 1


async def _retry_on_parse_failure_async(
    api_func,
    *,
    silent: bool = False,
    display=None,
    label: str | None = None,
    api_args: tuple = (),
    retry_func=None,
):
    """解析失败重试包装函数。

    内部先调用 _retry_api_call_async 获取结果，检查 usage 中的
    _parse_failed_ids；若非空，输出"解析参数错误正重新生成"提示并
    再次调用 _retry_api_call_async（最多重试 _MAX_PARSE_RETRIES 次）。

    重试用尽后从 usage 中弹出 _parse_failed_ids（避免泄漏到下游统计），
    返回部分成功的结果（成功解析的 tool_calls 正常流转）。

    此函数是 _retry_api_call_async 的外层包装，严格遵守安全约束：
    不修改 _retry_api_call_async 的内部逻辑。

    Args:
        api_func: 底层 API 调用函数
        retry_func: 可注入的 API 重试函数，默认使用 _retry_api_call_async。
                    用于单元测试中 mock 底层 API 调用，避免真实网络请求。
    """
    _retry = retry_func if retry_func is not None else _retry_api_call_async

    def _report(msg: str):
        if display is not None and label:
            display.update_model_phase(label, "error", msg)
        elif not silent:
            from ..core.constants import YELLOW, RESET
            locked_print(f"\n{YELLOW}{msg}{RESET}", flush=True)

    from .json_repair import _PARSE_RETRY_STATS, _JSON_REPAIR_LOCK

    result = await _retry(
        api_func,
        silent=silent, display=display, label=label,
        api_args=api_args,
    )
    reasoning_content, content, usage, tool_calls = result

    for attempt in range(_MAX_PARSE_RETRIES):
        failed_ids = usage.get("_parse_failed_ids", [])
        if not failed_ids:
            break
        # 更新解析重试统计：触发重试
        with _JSON_REPAIR_LOCK:
            _PARSE_RETRY_STATS["retry_triggered"] += 1
        _logger.warning(
            "解析失败，触发重试（第 %d 次）。失败 ID: %s",
            attempt + 1, failed_ids,
        )
        _report("解析参数错误正重新生成")
        result = await _retry(
            api_func,
            silent=silent, display=display, label=label,
            api_args=api_args,
        )
        reasoning_content, content, usage, tool_calls = result
        # 重试后检查是否仍失败 → 更新成功/耗尽统计
        if not usage.get("_parse_failed_ids"):
            with _JSON_REPAIR_LOCK:
                _PARSE_RETRY_STATS["retry_success"] += 1

    # 重试用尽后仍存在解析失败：记录 warning 日志（含失败 ID 列表；
    # 原始参数片段已在 _tool_parse_utils 解析层通过 _logger.warning 记录）
    failed_ids = usage.get("_parse_failed_ids", [])
    if failed_ids:
        with _JSON_REPAIR_LOCK:
            _PARSE_RETRY_STATS["retry_exhausted"] += 1
        success_ids = [tc.get("id", "?") for tc in tool_calls] if tool_calls else []
        _logger.warning(
            "解析重试用尽（共 %d 次），仍有 %d 个 tool_call 解析失败。"
            "失败 ID: %s，成功 ID: %s",
            _MAX_PARSE_RETRIES + 1, len(failed_ids), failed_ids, success_ids,
        )

    # 清理 _parse_failed_ids，避免泄漏到下游统计
    usage.pop("_parse_failed_ids", None)
    return reasoning_content, content, usage, tool_calls


# ── 公开接口 ────────────────────────────────────────────────

async def call_model_async(
    messages: list,
    model: str | None = None,
    tools: list | None = None,
    display=None,
    label: str | None = None,
    silent: bool = False,
) -> tuple:
    """异步流式调用模型。

    返回 (reasoning_content, content, usage, tool_calls)。
    与同步 call_model 接口完全兼容。
    """
    model = model or MODEL
    adapter = _get_adapter(model)
    messages_copy = copy.deepcopy(messages)
    messages_copy = adapter.prepare_messages(messages_copy, model)
    is_reasoner = adapter.is_reasoner_model(model)
    return await _retry_on_parse_failure_async(
        stream_call_async,
        silent=silent, display=display, label=label,
        api_args=(messages_copy, model, is_reasoner, tools, display, label, silent),
    )


async def call_model_sync_async(
    messages: list,
    model: str | None = None,
    tools: list | None = None,
    display=None,
    label: str | None = None,
) -> tuple:
    """异步非流式模型调用（Agent 内部使用），无终端输出。

    返回 (reasoning_content, content, usage, tool_calls)。
    """
    model = model or MODEL
    adapter = _get_adapter(model)
    messages_copy = copy.deepcopy(messages)
    messages_copy = adapter.prepare_messages(messages_copy, model)
    return await _retry_on_parse_failure_async(
        _call_sync_async,
        silent=True, display=display, label=label,
        api_args=(messages_copy, model, tools, display, label),
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

    adapter = _get_adapter(model)
    kwargs = adapter.build_request_kwargs(
        messages=messages,
        model=model,
        tools=tools,
    )

    start_time = time.time()
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
