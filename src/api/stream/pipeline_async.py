"""Async 流式处理 Pipeline — 基于 asyncio 的非阻塞 SSE 流解析

与同步版 pipeline.py 接口对等，但：
- 使用 async for 替代 sync for
- 使用 asyncio.Event 替代 threading.Event
- 使用 asyncio.Queue 替代 queue.Queue
- 使用 asyncio.sleep 替代 time.sleep
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from typing import AsyncIterator

from ..events import publish_event
from ..client_async import (
    chat_completions_async, chat_completions_async_anthropic, _CONNECTION_ERRORS,
)
from ..tokens import estimate_tokens
from ..interrupt_async import is_interrupted_async
from ..stats import (
    accumulate_usage, set_stream_speed,
    add_token_size,
    _notify_stream_started,
    _notify_stream_ended,
    _notify_stream_progress,
)
from ..stream_parse import convert_tool_calls_map_with_status
from .context import StreamContext
from .handlers import ReasoningHandler, ContentHandler, ToolCallsHandler, SpeedHandler
from ...core.constants import YELLOW, RESET
from ...tui.events.consumers import publish_output

_logger = logging.getLogger(__name__)

_STREAM_IDLE_TIMEOUT = 60.0
_INTERRUPTED_MSG = f"\n{YELLOW}  ● 已中断{RESET}"
_INTERRUPTED_MSG_TEXT = "(已中断)"


async def _interruptible_iter_async(
    response_iter: AsyncIterator[dict],
    ctx: StreamContext,
) -> AsyncIterator[dict]:
    """将 async generator 包装为可中断 + 带空闲超时的 async generator。

    相比于同步版的 threading + queue 方案，asyncio 版本：
    - 无需额外线程，直接 await 上游迭代
    - 通过 asyncio.sleep(0) 让出控制权给 Event Loop
    - 每次迭代后检查中断标志

    try/finally 显式清理 response_iter（而非 aclosing()），从而在
    finally 块中消化清理期间的异常，防止它们与传播中的 GeneratorExit
    组合成 BaseExceptionGroup（Python 3.11+），确保 async for 的
    aclose() 能正确识别生成器已关闭。

    使用 while 循环 + 手动 __anext__() 替代 async for，从而可以给每次
    迭代设置 asyncio.wait_for 超时。当 SSE 流因网络问题卡住时，
    抛出 StreamIdleTimeoutError（继承自 TimeoutError），由上游的
    _retry_api_call_async 重试机制自动捕获并重试。
    """
    try:
        while True:
            try:
                chunk = await asyncio.wait_for(
                    response_iter.__anext__(),
                    timeout=_STREAM_IDLE_TIMEOUT,
                )
            except asyncio.TimeoutError:
                # asyncio.TimeoutError 在 Python 3.11+ 等价于内置 TimeoutError，
                # StreamIdleTimeoutError 继承自 TimeoutError，会被上游重试机制捕获
                raise StreamIdleTimeoutError(
                    f"流空闲超时: {_STREAM_IDLE_TIMEOUT:.0f}秒内未收到新数据"
                )
            except StopAsyncIteration:
                return  # 流正常结束

            if await is_interrupted_async():
                return
            yield chunk
            # 每次 yield 后让出事件循环，给中断信号处理机会
            await asyncio.sleep(0)
    except asyncio.CancelledError:
        # CancelledError 降级为 StopAsyncIteration 安全退出。
        # 中断信号已通过 is_interrupted_async() 设置，
        # process() 中的后置检查会捕获到中断状态。
        # 关闭流迭代后正常结束：将 CancelledError 降级为 StopAsyncIteration，
        # 避免 asyncio 在 Task._step() 的 else 分支中对尚未消耗的
        # _must_cancel 重新抛出 CancelledError，从而导致 process()
        # 的 finally 块（_cleanup_display）在执行途中被中断。
        # 上层（stream_call_async / _execute_model_call_async）的
        # CancelledError 处理仍能正确感知中断状态。
        _logger.debug("_interruptible_iter_async caught CancelledError, exiting stream gracefully")
        ctx.task_cancelled = True  # 标记 task 被取消
        # ★ 保护：退出 async with aclosing(response_iter) 时，aclose()
        # 可能再次抛出 CancelledError。此处显式关闭迭代器并用
        # try/except 消化该异常，防止透出到 process() 上层。
        try:
            await response_iter.aclose()
        except (asyncio.CancelledError, StopAsyncIteration):
            pass
        return
    except StreamIdleTimeoutError:
        # 空闲超时/连接错误是预期可重试条件：降级为 WARNING 而非 ERROR，
        # 避免每轮重试刷出完整 traceback。交由上游 retry_api_call_async
        # 重试（默认最多 10 次）。
        _logger.warning("流空闲超时（%d秒内未收到新数据），交由重试层重试", _STREAM_IDLE_TIMEOUT)
        raise
    except _CONNECTION_ERRORS as e:
        _logger.warning("流式连接错误：%s，交由重试层重试", e)
        raise
    except Exception as e:
        # ★ 兜底：一律降级为 WARNING 而非 ERROR。上游 retry_api_call_async
        # 会重试（最多 10 次）并以 exc_info=True 记录完整 traceback；
        # 重试耗尽后 Pipeline 会以 ERROR 级别记录最终异常。此处避免
        # 每轮重试都刷出 ERROR + 完整 traceback。
        _logger.warning("Async stream iteration error: %s", e)
        raise
    finally:
        # 替代 aclosing()：显式清理 response_iter。
        # 消化清理期间的 CancelledError / StopAsyncIteration / Exception，
        # 防止它们与传播中的 GeneratorExit（由 async for 的 aclose() 注入）
        # 组合成 BaseExceptionGroup（Python 3.11+），
        # 导致 GeneratorExit 无法被 asyncio 识别为该生成器已正常关闭。
        try:
            await response_iter.aclose()
        except (asyncio.CancelledError, StopAsyncIteration, Exception):
            pass


def _extract_cancelled(exc: BaseException | None):
    """检查异常是否自身为 CancelledError 或 ExceptionGroup 中包含 CancelledError。"""
    if exc is None:
        return False
    if isinstance(exc, asyncio.CancelledError):
        return True
    subgroup = getattr(exc, "subgroup", None)
    if subgroup is not None:
        return subgroup(asyncio.CancelledError) is not None
    return False


class StreamIdleTimeoutError(TimeoutError):
    pass


class AsyncStreamPipeline:
    """Async 流式处理 Pipeline — 协调各 handler 处理 chunk。"""

    def __init__(self):
        self._reasoning_handler = ReasoningHandler()
        self._content_handler = ContentHandler()
        self._tool_calls_handler = ToolCallsHandler()
        self._speed_handler = SpeedHandler()

    async def process(
        self,
        ctx: StreamContext,
        response_iter: AsyncIterator[dict],
        silent: bool,
    ) -> tuple:
        """处理完整的异步流式响应流。

        Args:
            ctx: StreamContext（共享状态）
            response_iter: 异步流式响应迭代器
            silent: 静默模式

        Returns:
            (reasoning_content, content, usage, tool_calls)
        """
        _last_progress = 0.0
        _PROGRESS_INTERVAL = 0.1  # 100ms = 10次/秒
        try:
            async for chunk in _interruptible_iter_async(response_iter, ctx):
                # task_cancelled 降级路径：_interruptible_iter_async 捕获到
                # CancelledError 后标记 ctx.task_cancelled 并 return 结束迭代，
                # process() 的 for 循环自然结束。此处的检查主要是兜底，
                # 确保 task_cancelled 标记被正确处理。
                if ctx.task_cancelled or await is_interrupted_async():
                    ctx.esc_interrupted = True
                    if not silent:
                        publish_output(_INTERRUPTED_MSG, level="raw")
                    break

                # ── 流式进度刷新：10次/秒 ──
                now = time.perf_counter()
                if now - _last_progress >= _PROGRESS_INTERVAL:
                    _last_progress = now
                    _notify_stream_progress()

                # 处理 usage
                self._handle_usage(ctx, chunk)

                choices = chunk.get("choices")
                if not choices:
                    continue
                try:
                    first = choices[0]
                except (IndexError, TypeError):
                    continue

                delta = first.get("delta", {}) if isinstance(first, dict) else {}
                if not delta:
                    continue

                # reasoning_content
                rc = delta.get("reasoning_content")
                if rc and ctx.is_reasoning:
                    rc_tokens = estimate_tokens(rc)
                    self._reasoning_handler.handle(ctx, rc, rc_tokens)
                    self._speed_handler.try_update(ctx)
                    add_token_size(rc_tokens)

                # content
                dc = delta.get("content")
                if dc:
                    # 🔥 在 content 首次出现（推理→内容过渡）前，先 flush reasoning 缓冲区，
                    # 确保最后几 tok 推理内容在 PhaseDoneEvent("reasoning") 之前发出。
                    if ctx.is_reasoning:
                        self._reasoning_handler.flush(ctx.label)
                    dc_tokens = estimate_tokens(dc)
                    self._content_handler.handle(ctx, dc, dc_tokens)
                    self._speed_handler.try_update(ctx)
                    add_token_size(dc_tokens)

                # tool_calls
                dtc = delta.get("tool_calls")
                if dtc:
                    # 🔥 修复：先刷新 reasoning/content 缓冲区，确保最后几 tok
                    # 在 PhaseDoneEvent("content") 之前作为 ContentChunkEvent/
                    # ReasoningChunkEvent 发出，避免前端收到顺序颠倒导致最后
                    # 几个 token"丢失"（实际是还在缓冲区没 flush）。
                    self._content_handler.flush(ctx.label)
                    self._reasoning_handler.flush(ctx.label)
                    # 🔥 推理→工具调用过渡：发布 PhaseDoneEvent("reasoning")，
                    # 确保 ChatUI 在工具 spinner 输出之前关闭/刷新推理渲染器。
                    # 此前仅在 content.py 首次 content 到达时发布，遗漏了
                    #「思考→直接工具调用（无 content）」路径，导致思考内容
                    # 延迟渲染，工具输出先于思考内容到达终端。
                    if ctx.is_reasoning:
                        ctx.is_reasoning = False
                        # 🔥 发布 PhaseDoneEvent("reasoning")（去重助手：每流恰一次）
                        ctx.publish_phase_done_once("reasoning")
                    await self._tool_calls_handler.handle(ctx, dtc)
                    # ★ 2026-08-16 修复（多轮工具循环「最后一行不显示」）：
                    #   工具调用后模型将继续输出新一轮内容（新一轮推理/回答）。
                    #   阶段完成标志（phase_done_*_sent）重置为 False——否则
                    #   新一轮内容结束时 ``_cleanup_display`` 的
                    #   ``publish_phase_done_once`` 因「每流至多一次」去重跳过，
                    #   close_reasoning/close_content 不会再次执行，新一轮内容
                    #   尾部（最后一行无换行符）滞留在解析器缓冲永不渲染。
                    #   ★ 工具调用前的当前阶段已完成（reasoning done 已发布、
                    #     content done 由 tool_calls_handler 按 content_full
                    #     发布）——重置只影响「工具调用后」的新一轮，不破坏
                    #   当前阶段关闭（关闭幂等，重复发布无害）。
                    ctx.phase_done_reasoning_sent = False
                    ctx.phase_done_content_sent = False
                    self._speed_handler.try_update(ctx)
                    continue

                # 无匹配字段
                self._speed_handler.try_update(ctx)

            if not ctx.esc_interrupted and (ctx.task_cancelled or await is_interrupted_async()):
                ctx.esc_interrupted = True
                if not silent:
                    publish_output(_INTERRUPTED_MSG, level="raw")
        finally:
            self._speed_handler.final_update(ctx)
            await self._cleanup_display(ctx)

        return self._build_result(ctx)

    def _handle_usage(self, ctx: StreamContext, chunk: dict) -> None:
        """处理 usage chunk — 以真实值覆盖估计值。"""
        chunk_usage = chunk.get("usage")
        if chunk_usage and not ctx.usage_accumulated:
            # 兼容两种 usage 格式：
            # - 原始 OpenAI/DeepSeek 格式：prompt_tokens / completion_tokens +
            #   prompt_cache_hit_tokens / prompt_cache_miss_tokens
            # - 统一格式（Anthropic 转换层 / parse_response 产物）：input / output +
            #   input_cache_hit / input_cache_miss
            if "prompt_tokens" in chunk_usage or "completion_tokens" in chunk_usage:
                real_input = chunk_usage.get("prompt_tokens", 0)
                real_output = chunk_usage.get("completion_tokens", 0)
                from ..adapters.base import _extract_cache_usage
                real_hit, real_miss = _extract_cache_usage(chunk_usage)
            else:
                real_input = chunk_usage.get("input", 0)
                real_output = chunk_usage.get("output", 0)
                real_hit = chunk_usage.get("input_cache_hit", 0)
                real_miss = chunk_usage.get("input_cache_miss", 0)
            ctx.usage["input"] = real_input
            ctx.usage["output"] = real_output
            ctx.usage["input_cache_hit"] = real_hit
            ctx.usage["input_cache_miss"] = real_miss

            # SpeedHandler.try_update() 已在流式过程中累积了估计的
            # output token。此处以真实值直接覆盖，确保统计准确。
            # 修正值 = 真实值 - 估计值；合并为单次 accumulate_usage：
            # input += real_input，output += correction，
            # calls 恰 +1（对应一次真实 API 调用）。
            # 此前 split 为两次调用导致 calls 多计 1 次（/cost 调用次数虚高）。
            estimated_output = ctx.last_live_est
            correction = real_output - estimated_output
            # ★ 已用真实值修正，重置 token 估计使后续 final_update 不再产生 delta
            ctx.token_estimate = 0
            ctx.last_live_est = 0
            # ★ 标记最终 usage 已接收，后续 SpeedHandler 跳过重复累积
            ctx.final_usage_received = True
            accumulate_usage({
                "input": real_input,
                "output": correction,
                "input_cache_hit": real_hit,
                "input_cache_miss": real_miss,
            })
            ctx.usage_accumulated = True

    def _build_result(self, ctx: StreamContext) -> tuple:
        """构建最终返回结果（与同步版逻辑一致）。"""
        reasoning = ctx.reasoning_full
        content = ctx.content_full

        ctx.usage["tool_parse_elapsed"] = ctx.tracker.elapsed

        if ctx.esc_interrupted:
            if not content:
                content = _INTERRUPTED_MSG_TEXT
            elif _INTERRUPTED_MSG_TEXT not in content:
                content += f" ({_INTERRUPTED_MSG_TEXT})"
            # ★ 修复：中断时丢弃不完整的工具调用，避免下游执行损坏的 tool_calls
            ctx.usage.pop("_parse_failed_ids", None)
            return reasoning, content, ctx.usage, []

        if ctx.tracker.interrupted:
            if not content:
                content = _INTERRUPTED_MSG_TEXT
            elif _INTERRUPTED_MSG_TEXT not in content:
                content += f" ({_INTERRUPTED_MSG_TEXT})"
            # ★ 修复：tracker.interrupted（工具参数接收中断）时，
            # tool_calls_map 中的 arguments 可能是不完整的 JSON 片段，
            # convert_tool_calls_map 中的 json_loads_safe 可能返回
            # 部分解析结果，导致下游执行残缺的工具调用。
            # 与 esc_interrupted 路径一致，丢弃不完整的工具调用。
            ctx.usage.pop("_parse_failed_ids", None)
            return reasoning, content, ctx.usage, []

        tool_calls, failed_ids = convert_tool_calls_map_with_status(ctx.tool_calls_map)
        if failed_ids:
            ctx.usage["_parse_failed_ids"] = failed_ids
        if not ctx.silent:
            self._print_usage_summary(ctx)

        if not content and not reasoning and not ctx.tool_calls_map:
            content = "(无内容)"

        return reasoning, content, ctx.usage, tool_calls

    async def _cleanup_display(self, ctx: StreamContext) -> None:
        """清理显示（全异步）。"""
        # ★ 幂等保护：已被调用过则跳过，防止 process() finally 与
        #   stream_call_async except CancelledError 重复调用
        if ctx._cleaned_up:
            return
        ctx._cleaned_up = True

        # ── 第 1 步：刷出剩余的 EventBus 缓冲事件 ───────────
        # flush 顺序：reasoning 尾事件先于 content 尾事件（推理→内容过渡顺序），
        # 且均先于后续 PhaseDoneEvent 发布——命令队列优先级（REASONING/CONTENT
        # 与 PhaseDone 同级 0）+ seq 保序保证渲染线程先渲染内容命令再渲染完成命令。
        self._reasoning_handler.flush(ctx.label)
        self._content_handler.flush(ctx.label)

        # ⏳ 单次 await asyncio.sleep(0) 仅为事件循环让出（保留 CancelledError
        # 保护）；事件顺序由队列优先级 + seq 保证——flush 为同步 publish
        # （DisplayEventBus 无批处理启用）→ 内容命令先于 PhaseDoneCmd 入队。
        # 后续所有 publish_event 调用不再额外 sleep(0)。
        # ★ 保护：CancelledError 不跳过后续清理，防止渲染器泄漏
        try:
            await asyncio.sleep(0)
        except asyncio.CancelledError:
            # 有意吞掉：确保渲染器清理不跳过。后续清理代码均为同步操作，
            # 不会再次触发 CancelledError。
            _logger.debug("_cleanup_display: 被取消，继续执行同步清理")
        except BaseException:
            if _extract_cancelled(sys.exc_info()[1]):
                _logger.debug("_cleanup_display: 被取消 (in group)，继续执行同步清理")
            else:
                raise

        # ── 第 2a 步：清除 tracker 显示行（合并到尾部换行处理）──
        # 为避免 publish_output 附加的 \n 导致多余空行，
        # tracker 行清除已合并到尾部换行步骤统一处理。

        # ── 第 2b 步：发布 PhaseDoneEvent（即使 silent=True 也要发） ─
        # ★ 标记追踪：在 content.py 或 tool_calls.py 中已发布的阶段事件，此处不再重复发送
        #   PhaseDoneEvent("reasoning") 由 content.py 在首次 content 到达时发布，
        #   PhaseDoneEvent("content") 由 tool_calls.py 在首次工具调用时发布。
        #   统一经 ctx.publish_phase_done_once() 去重（每流同 phase 至多一次）。
        #   ★ 修正（2026-07-31）：不再误用 phase_thinking_sent 判断「是否已发
        #     PhaseDone("reasoning")」——phase_thinking_sent 由 reasoning.py 在
        #     首个推理 chunk 置位（语义为「thinking 阶段已宣布」），与 PhaseDone
        #     发布无关；reasoning-only 流（有推理、无 content、无工具）此前因此
        #     从不发布 PhaseDone("reasoning")，导致 close_reasoning() 不执行、
        #     推理尾部无换行 token 滞留 parser 缓冲永不渲染。改为「始终尝试 +
        #     去重助手」：content.py 已发布时幂等跳过，reasoning-only 流在收尾
        #     必发布一次。
        if ctx.reasoning_full:
            ctx.publish_phase_done_once("reasoning")
        if ctx.content_full and not ctx.esc_interrupted:
            # ★ 2026-08-16 修复（多轮工具循环「最后一行不显示」）：
            #   不再要求 ``not ctx.tool_calls_map``——工具调用时 content 可能
            #   为空（推理后直接工具调用，tool_calls.py 未发布 content done），
            #   该条件会阻止流结束时发布 → 工具调用后的内容（内容B）无法关闭
            #   渲染通道，尾部无换行 token 滞留 parser 缓冲永不渲染。去掉后
            #   统一「始终尝试 + 去重助手」：已发布过（tool_calls.py）幂等跳过，
            #   未发布过（工具后内容）必发布一次。``not ctx.esc_interrupted``
            #   必须保留（中断文本 ContentChunk 晚于 PhaseDone 到达被丢弃）。
            ctx.publish_phase_done_once("content")

        # 🔥 中断标记：向前端发送 (已中断) 标记
        if ctx.esc_interrupted:
            if ctx.content_full:
                publish_event("ContentChunkEvent", text=f" ({_INTERRUPTED_MSG_TEXT})",
                              label=ctx.label or "")
            else:
                publish_event("ContentChunkEvent", text=_INTERRUPTED_MSG_TEXT,
                              label=ctx.label or "")
            # 🔥 发布 PhaseDoneEvent("content")（去重助手：每流恰一次）
            ctx.publish_phase_done_once("content")

        # 🔥 有工具调用时，先闭合 content 气泡，再发送 segment_end 信号。
        #    中断时不发送 segment_end：不完整工具调用不应触发完成信号。
        #    工具参数接收中断（tracker.interrupted）同样不发送：不完整参数不应触发生成信号。
        if ctx.tool_calls_map and not ctx.esc_interrupted and not ctx.tracker.interrupted:
            if ctx.content_full:
                # 🔥 发布 PhaseDoneEvent("content")（去重助手：tool_calls.py 已置位时幂等跳过）
                ctx.publish_phase_done_once("content")
            publish_event("PhaseDoneEvent", label=ctx.label or "", phase="segment_end")

        # ⏳ 再次保护：tracker.finalize() 内部有 asyncio.sleep(0.2) 取消 Task，
        # 如果被取消跳过，tracker 的 _update_loop_async Task 泄漏。
        try:
            await ctx.tracker.finalize()
        except asyncio.CancelledError:
            _logger.warning("_cleanup_display: tracker.finalize 被取消，强制清理")
            # 兜底强制取消 tracker task
            if ctx.tracker._task is not None:
                ctx.tracker._task.cancel()
            ctx.tracker._task = None
        except BaseException:
            if _extract_cancelled(sys.exc_info()[1]):
                _logger.warning("_cleanup_display: tracker.finalize 被取消 (in group)，强制清理")
                if ctx.tracker._task is not None:
                    ctx.tracker._task.cancel()
                ctx.tracker._task = None
            else:
                raise

        if not ctx.silent and (ctx.content_full or ctx.reasoning_full):
            text = "\r\033[K" if ctx.tracker.started else ""
            publish_output(text, level="raw")

    def _print_usage_summary(self, ctx: StreamContext) -> None:
        """打印使用量摘要。"""
        if ctx.usage["input"] <= 0 and ctx.usage["output"] <= 0:
            return
        elapsed = time.perf_counter() - ctx.stream_start_time
        speed = ctx.usage["output"] / elapsed if elapsed > 0 and ctx.usage["output"] > 0 else 0.0
        ctx.usage["speed"] = speed
        set_stream_speed(speed)

        if ctx.display and ctx.label:
            ctx.display.update_speed(ctx.label, speed)


# ── async 流式调用入口 ─────────────────────────────────────

async def stream_call_async(
    messages: list,
    model: str,
    is_reasoner: bool,
    tools: list | None = None,
    display=None,
    label: str | None = None,
    silent: bool = False,
) -> tuple:
    """异步流式调用模型并实时渲染输出。

    与同步 stream_call 接口对等。

    Args:
        is_reasoner: 是否推理模型
        silent: 静默模式，跳过所有终端输出

    返回 (reasoning_content, content, usage, tool_calls)。
    """
    from ...config import MODEL as default_model
    model = model or default_model

    # 通过适配器构建请求参数（含 thinking 参数注入等适配逻辑）
    from .._adapter_manager import get_adapter
    adapter = get_adapter(model)
    kwargs = adapter.build_request_kwargs(
        messages=messages,
        model=model,
        tools=tools,
        stream=True,
        stream_options={"include_usage": True},
    )

    ctx = StreamContext(model, display, label, silent)
    pipeline = AsyncStreamPipeline()

    # 流式开始时估算输入 token
    if display and label:
        input_est = sum(estimate_tokens(m.get("content", "") or "") for m in messages)
        display.update_live_input(label, input_est)

    try:
        _notify_stream_started()
        if getattr(adapter, '_protocol', '') == 'anthropic':
            raw_iter = await chat_completions_async_anthropic(
                base_url=adapter._base_url, **kwargs)
            # ★ 转换层：将 Anthropic SSE chunks 转换为统一格式（OpenAI 兼容），
            #    使 AsyncStreamPipeline.process() 能按 choices[0].delta.content 路径解析。
            # ★ 并发安全：为每条流创建独立的工具累积状态 dict——适配器实例被
            #    _adapter_manager 按模型缓存共享，实例级 _stream_tool_acc 会被
            #    并发流交叉污染（工具参数串流），故显式传入每流独立 state。
            async def _anthropic_to_unified(raw):
                tool_acc: dict = {}
                async for chunk in raw:
                    yield adapter.parse_stream_chunk(chunk, tool_acc)
            response_iter = _anthropic_to_unified(raw_iter)
        else:
            response_iter = await chat_completions_async(**kwargs)
        # response_iter 是 AsyncIterator[dict]（统一格式）
        return await pipeline.process(ctx, response_iter, silent)
    except asyncio.CancelledError:
        # DEBUG 级别而非 WARNING：取消是正常流程（中断关闭），非错误事件
        _logger.debug("stream_call_async 被取消，返回已累积内容")
        result = pipeline._build_result(ctx)
        # ★ 补调 _cleanup_display：process() 的 finally 块在 CancelledError
        # 直接抛到 stream_call_async 时不会执行，导致渲染器泄漏、
        # PhaseDoneEvent 不发布、tracker task 泄漏。
        # 但仅当 process() 尚未执行 _cleanup_display 时才补调。
        # 通过在 _cleanup_display() 中添加幂等保护，只在此处未被
        # process() finally 调用过时才补调。
        if not ctx._cleaned_up:
            try:
                await pipeline._cleanup_display(ctx)
            except asyncio.CancelledError:
                pass
            except BaseException:
                if _extract_cancelled(sys.exc_info()[1]):
                    pass
                else:
                    raise
        return result
    except KeyboardInterrupt:
        raise
    except BaseException as e:
        # Python 3.11+: BaseExceptionGroup 包装的 CancelledError（例如
        # 清理期间 aclose() 被取消）不会匹配 except CancelledError。
        if _extract_cancelled(e):
            _logger.debug("stream_call_async 被取消 (in group)，返回已累积内容")
        elif not ctx.content_full and not ctx.reasoning_full and not ctx.tool_calls_map:
            # ★ 尚未产出任何内容（首个 SSE 块前就失败/超时）：重新抛出，
            # 交由 retry_api_call_async 重试（默认最多 10 次）。此时重启流
            # 不会重复渲染任何内容，幂等安全。process() 的 finally 已执行
            # _cleanup_display，无需重复清理。
            _logger.warning("stream_call_async 流式异常（未产出内容）：%s，交由重试层重试", e)
            raise
        else:
            # ★ 已产出部分内容：返回已累积内容（重启流会重复渲染已显示内容，
            # 故不重试；保留已接收的 tool_calls / content 不丢失）。
            _logger.warning("stream_call_async 流式异常（已产出部分内容）：%s，返回已累积内容", e)
        result = pipeline._build_result(ctx)
        if not ctx._cleaned_up:
            try:
                await pipeline._cleanup_display(ctx)
            except asyncio.CancelledError:
                pass
            except BaseException:
                if _extract_cancelled(sys.exc_info()[1]):
                    pass
                else:
                    raise
        return result
    finally:
        _notify_stream_ended()


__all__ = ["stream_call_async", "AsyncStreamPipeline", "StreamIdleTimeoutError"]
