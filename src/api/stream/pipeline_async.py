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
from ..client_async import chat_completions_async, chat_completions_async_anthropic
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
from ...ui._lock import locked_print

_logger = logging.getLogger(__name__)

_STREAM_IDLE_TIMEOUT = 30.0
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
    except Exception:
        _logger.exception("Async stream iteration error")
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
                        locked_print(_INTERRUPTED_MSG, flush=True)
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
                        publish_event("PhaseDoneEvent",
                                      label=ctx.label or "", phase="reasoning")
                    await self._tool_calls_handler.handle(ctx, dtc)
                    self._speed_handler.try_update(ctx)
                    continue

                # 无匹配字段
                self._speed_handler.try_update(ctx)

            if not ctx.esc_interrupted and (ctx.task_cancelled or await is_interrupted_async()):
                ctx.esc_interrupted = True
                if not silent:
                    locked_print(_INTERRUPTED_MSG, flush=True)
        finally:
            self._speed_handler.final_update(ctx)
            await self._cleanup_display(ctx)

        return self._build_result(ctx)

    def _handle_usage(self, ctx: StreamContext, chunk: dict) -> None:
        """处理 usage chunk — 以真实值覆盖估计值。"""
        chunk_usage = chunk.get("usage")
        if chunk_usage and not ctx.usage_accumulated:
            real_input = chunk_usage.get("prompt_tokens", 0)
            real_output = chunk_usage.get("completion_tokens", 0)
            ctx.usage["input"] = real_input
            ctx.usage["output"] = real_output

            # SpeedHandler.try_update() 已在流式过程中累积了估计的
            # output token。此处以真实值直接覆盖，确保统计准确。
            # 由于 accumulate_usage 是累加操作，先追加真实值，
            # 再追加负的修正值，最后结果 = 估计值 + (真实值 - 估计值) = 真实值。
            # 修正值在首次遇到 usage 时只做一次。
            estimated_output = ctx.last_live_est
            correction = real_output - estimated_output
            if correction != 0:
                accumulate_usage({"output": correction})
            # ★ 已用真实值修正，重置 token 估计使后续 final_update 不再产生 delta
            ctx.token_estimate = 0
            ctx.last_live_est = 0
            # ★ 标记最终 usage 已接收，后续 SpeedHandler 跳过重复累积
            ctx.final_usage_received = True
            accumulate_usage({"input": real_input})
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
        self._reasoning_handler.flush(ctx.label)
        self._content_handler.flush(ctx.label)

        # ⏳ 单次 await asyncio.sleep(0)，让事件循环有机会处理
        # 已排队的 ContentChunkEvent/ReasoningChunkEvent task，
        # 确保最后一批 chunk 先于 PhaseDoneEvent 到达前端。
        # 后续所有 publish_event 调用不再额外 sleep(0)，
        # 利用 EventBus 同步发布特性，在单次调度后按序发送。
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

        # ── 第 2a 步：清除 tracker 显示行（仅在非 silent 模式，ChatUI 活跃时跳过） ─
        if not ctx.silent and ctx.tracker.started:
            from ...chat_ui import get_active_chat_ui  # noqa: PLC0415
            if get_active_chat_ui() is None:
                locked_print("\r\033[K", end="", flush=True, file=sys.__stdout__)

        # ── 第 2b 步：发布 PhaseDoneEvent（即使 silent=True 也要发，确保 WebUI 收到） ─
        # ★ 标记追踪：在 content.py 或 tool_calls.py 中已发布的阶段事件，此处不再重复发送
        #   PhaseDoneEvent("reasoning") 由 content.py 在首次 content 到达时发布，
        #   PhaseDoneEvent("content") 由 tool_calls.py 在首次工具调用时发布。
        #   使用 _phase_done_reasoning_sent / _phase_done_content_sent 标记避免重复。
        if ctx.reasoning_full and not ctx.phase_thinking_sent:
            # phase_thinking_sent 在首次 content 到达时被 content.py 置 True，
            # 因此若它仍为 False → PhaseDoneEvent("reasoning") 尚未被发布过。
            publish_event("PhaseDoneEvent", label=ctx.label or "", phase="reasoning")
        if ctx.content_full and not ctx.tool_calls_map and not ctx.esc_interrupted:
            # 仅当没有工具调用路径且未被中断时才发布 content done 事件
            # (tool_calls.py 已发布过 content done, 此处跳过)
            publish_event("PhaseDoneEvent", label=ctx.label or "", phase="content")

        # 🔥 中断标记：向前端发送 (已中断) 标记
        if ctx.esc_interrupted:
            if ctx.content_full:
                publish_event("ContentChunkEvent", text=f" ({_INTERRUPTED_MSG_TEXT})",
                              label=ctx.label or "")
            else:
                publish_event("ContentChunkEvent", text=_INTERRUPTED_MSG_TEXT,
                              label=ctx.label or "")
            publish_event("PhaseDoneEvent", label=ctx.label or "", phase="content")

        # 🔥 有工具调用时，先闭合 content 气泡，再发送 segment_end 信号。
        #    中断时不发送 segment_end：不完整工具调用不应触发完成信号。
        #    工具参数接收中断（tracker.interrupted）同样不发送：不完整参数不应触发生成信号。
        if ctx.tool_calls_map and not ctx.esc_interrupted and not ctx.tracker.interrupted:
            if ctx.content_full:
                publish_event("PhaseDoneEvent", label=ctx.label or "", phase="content")
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
            from ...chat_ui import get_active_chat_ui  # noqa: PLC0415
            if get_active_chat_ui() is None:
                locked_print(flush=True, file=sys.__stdout__)

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
    from ..model_async import _get_adapter
    adapter = _get_adapter(model)
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
            async def _anthropic_to_unified(raw):
                async for chunk in raw:
                    yield adapter.parse_stream_chunk(chunk)
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
        raise
    finally:
        _notify_stream_ended()


__all__ = ["stream_call_async", "AsyncStreamPipeline", "StreamIdleTimeoutError"]
