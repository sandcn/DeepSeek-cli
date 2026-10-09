"""Async 模型调用公开接口 — 与同步版 model.py 接口对等

提供 async call_model_async / call_model_sync_async，
内部使用 httpx.AsyncClient 实现非阻塞 I/O。
"""

from __future__ import annotations

import copy
import json
import logging
import time

from .client_async import (
    chat_completions_async, chat_completions_async_anthropic,
)
from .tokens import estimate_tokens
from .interrupt_async import is_interrupted_async
from .stream.pipeline_async import stream_call_async
from ..core.stats import (
    accumulate_usage, set_tool_parse_elapsed, set_stream_speed,
    add_token_size_batch, record_generation_rate,
)
from ..config import MODEL
from ..core.constants import STREAM_LABEL_SUMMARIZE
from ._retry import retry_api_call_async, retry_on_parse_failure_async
from ._adapter_manager import get_adapter
from .image_upload import optimize_messages_for_upload

_logger = logging.getLogger(__name__)


def _optimize_messages_for_upload(messages: list) -> dict:
    """上传前图片瘦身（「一切皆插件」：内核 ``ctx.multimodal`` 优先，回退直接调用）。

    保持模块级 ``optimize_messages_for_upload`` 仍为可 monkeypatch 的回退实现。
    """
    try:
        from ..kernel.runtime import active_service

        service = active_service("multimodal")
        if service is not None:
            return service.optimize_messages_for_upload(messages)
    except Exception:
        pass
    return optimize_messages_for_upload(messages)

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
    # 上传前图片瘦身（折叠/压缩/缓存）：缓解多图场景每轮请求重复上传全量
    # base64 导致的卡顿；只作用于发送副本，不影响 agent.messages。
    _optimize_messages_for_upload(messages_copy)
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
    # 上传前图片瘦身（折叠/压缩/缓存）：同 call_model_async。
    _optimize_messages_for_upload(messages_copy)
    return await retry_on_parse_failure_async(
        _call_sync_async,
        silent=True, display=display, label=label,
        api_args=(messages_copy, model, tools, display, label),
        override_max_retries=override_max_retries,
        fixed_delay_sec=fixed_delay_sec,
    )

# ── 流式摘要调用（上下文压缩等内部调用） ────────────────────

async def call_model_summarize_async(
    messages: list,
    model: str | None = None,
    override_max_retries: int | None = None,
    fixed_delay_sec: float | None = None,
) -> tuple:
    """异步**流式**摘要调用（上下文压缩摘要等内部调用专用）。

    与 ``call_model_async`` 走同一条 SSE 流式管线，但固定为该内部调用的口径：

    - ``silent=True``：不向终端渲染摘要生成过程（内部调用，用户无需看到）；
    - ``label=STREAM_LABEL_SUMMARIZE``：内容/阶段事件不进入主 Agent 或
      SubAgent 的渲染通道（label 不匹配），且不计入 ``main · N%`` 上下文
      使用率（core 侧按该标签跳过，见 ``core.context_manager``）；
    - 不传工具（摘要只产出文本）。

    为何用流式：非流式长输出在服务端/网关侧可能因连接空闲被截断，且拿不到
    生成过程中的实时 token；流式管线天然规避长连接空闲问题，并复用与主对话
    完全一致的真实 usage 校准与 token 统计口径（生成中即可见 tok/s）。

    Returns:
        ``(reasoning_content, content, usage, tool_calls)``。
    """
    started = time.time()
    reasoning, content, usage, tool_calls = await call_model_async(
        messages,
        model=model,
        tools=None,
        display=None,
        label=STREAM_LABEL_SUMMARIZE,
        silent=True,
        override_max_retries=override_max_retries,
        fixed_delay_sec=fixed_delay_sec,
    )
    # 推理模型兼容：仅返回 reasoning（无 content）时以 reasoning 作为摘要文本
    # ——与非流式路径 ``_call_sync_async`` 的兜底语义保持一致。
    if reasoning and not content and not tool_calls:
        content = reasoning
    # ★ 生成速率登记（不改动总 tok）：流式期间 token 已实时累加并经真实 usage
    #   校正；此处登记本次生成的真实平均速率，使状态栏「tok/s」在压缩结束后的
    #   宽限期内仍显示本次摘要生成速度（而非随 1 秒窗口滑走归零）。
    try:
        output_tokens = int((usage or {}).get("output", 0) or 0)
    except (TypeError, ValueError):
        output_tokens = 0
    elapsed = time.time() - started
    if output_tokens > 0 and elapsed > 0:
        record_generation_rate(output_tokens, elapsed)
    return reasoning, content, usage, tool_calls

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

    # ★ 真实输入 token 校准上下文使用率（2026-10「main 上下文百分比统计
    #   不准」修复）——非流式路径同样把 usage.input 交给 core 作权威基线。
    try:
        from .stream._usage_hook import notify_prompt_usage
        notify_prompt_usage(usage.get("input", 0), label)
    except Exception:
        _logger.debug("真实 prompt token 校准上下文使用率失败", exc_info=True)

    accumulate_usage(usage)
    # ★ 非流式调用（直接调用 call_model_sync 的场景，非压缩摘要——压缩摘要走
    #   流式管线 call_model_summarize_async）的生成 token 计入状态栏
    #   「总tok / tok/s」：本调用结束时才拿到真实 usage，故以「已知耗时的批量
    #   生成」形式计入——总 tok 一次性累加（历史累计语义不变），tok/s 回退到
    #   真实平均速率（output / api_duration），避免整批算进最后一个采样间隔
    #   而虚高、或随 1 秒窗口滑走而瞬间归零（用户看不到这次生成的速率）。
    add_token_size_batch(usage.get("output", 0), api_duration)

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

from ._model_loops import _get_model_loop

def call_model_sync(messages, model=None, tools=None, display=None, label=None):
    """同步兼容包装 — 在线程持久化事件循环中运行 async 调用。

    供 commands.py / context_manager.py 等尚未迁移到 async 的模块使用。
    """
    loop = _get_model_loop()
    return loop.run_until_complete(
        call_model_sync_async(messages, model, tools, display, label),
    )

def call_model_summarize_sync(messages, model=None):
    """同步**流式**摘要调用包装（上下文压缩摘要等内部调用专用）。

    在调用线程的持久化事件循环中运行 ``call_model_summarize_async``——压缩
    引擎（``core.compaction``）在线程中同步调用摘要函数，经本包装获得与
    主对话一致的流式管线行为（silent + 内部 label，不渲染、不计入主
    Agent 上下文使用率）。
    """
    loop = _get_model_loop()
    return loop.run_until_complete(
        call_model_summarize_async(messages, model),
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