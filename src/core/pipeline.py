"""Agent Pipeline — 可编排的中间件管道

将 Agent._run_one_round() 的硬编码循环升级为可编排的中间件链。
核心模型调用-工具循环逻辑内建于 Pipeline，
中间件通过 before/after 钩子注册自定义逻辑。

异步路径使用 AsyncMiddleware 基类，所有钩子均为 async def。

使用方式:
    # 异步
    ctx = PipelineContext(agent)
    pipeline = Pipeline()
    pipeline.use_async(AsyncObservabilityMiddleware())
    interrupted = await pipeline.run_round_async(ctx)
"""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import field
from src._compat import dataclass
from typing import Any

from .internal.agent import _event_facets
from .compaction.errors import is_context_overflow_error

_logger = logging.getLogger(__name__)

_INTERRUPTED_MSG = "(已中断)"

#: 上下文溢出恢复的重试上限（与 dsh compaction-basic 默认一致）。
_MAX_OVERFLOW_RETRIES = 1

# ═══════════════════════════════════════════════════════════════
# PipelineContext
# ═══════════════════════════════════════════════════════════════

@dataclass(slots=True)
class PipelineContext:
    """流水线上下文 — 流经整个管线的状态容器

    在 pipeline.run_round_async() 执行期间，
    每个生命周期钩子都可以读取和修改此对象。

    字段说明：
    - checkpoint_requested: 当 CancelledError 发生时设为 True（Bug 4），
      供 session._emit_round_events 检查以保存 checkpoint。
    """

    agent: "Agent"
    interrupted: bool = False
    round_complete: bool = False

    # ★ Bug4: Pipeline CancelledError 时请求保存 checkpoint 的标记
    checkpoint_requested: bool = False

    # 当前模型调用的结果
    reasoning: str = ""
    content: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    tool_calls: list[dict] = field(default_factory=list)

    # 本轮模型调用次数
    model_calls: int = 0

    # 未捕获异常
    error: Exception | None = None

    # session_state_machine 由外部设置（agent.py），init=False 使 __init__ 不要求传参
    session_state_machine: Any = field(default=None, init=False)

    # interrupt_port 由外部设置（agent.py 在构造 PipelineContext 时传入），
    # init=False 使 __init__ 不要求传参，保持向后兼容
    interrupt_port: Any = field(default=None, init=False)

# ═══════════════════════════════════════════════════════════════
# AsyncMiddleware（异步中间件基类）
# ═══════════════════════════════════════════════════════════════

class AsyncMiddleware:
    """异步中间件基类

    所有钩子均为 async def。
    Pipeline.run_round_async() 调用此版本的钩子。
    """

    @property
    def name(self) -> str:
        """中间件名称（默认使用类名）"""
        return self.__class__.__name__

    async def before_model_call(self, ctx: PipelineContext) -> None:
        """异步：模型调用前的钩子"""
        pass

    async def after_model_call(self, ctx: PipelineContext) -> None:
        """异步：模型调用后的钩子"""
        pass

    async def before_tool_execution(self, ctx: PipelineContext) -> None:
        """异步：工具执行前的钩子"""
        pass

    async def after_tool_execution(self, ctx: PipelineContext) -> None:
        """异步：工具执行后的钩子"""
        pass

    async def on_round_complete(self, ctx: PipelineContext) -> None:
        """异步：一轮对话完成时的钩子"""
        pass

    async def on_exception(self, ctx: PipelineContext, exc: Exception) -> None:
        """异步：未捕获异常时的钩子"""
        pass

# ═══════════════════════════════════════════════════════════════
# Pipeline（流水线编排器）
# ═══════════════════════════════════════════════════════════════

class Pipeline:
    """Agent 对话流水线 — 编排中间件链

    核心逻辑（模型调用 + 工具处理循环）内建于 _execute_model_call_async()，
    中间件通过注册生命周期钩子来扩展行为。

    使用方式:
        pipeline = Pipeline()
        pipeline.use_async(SomeAsyncMiddleware())
        pipeline.use_async(AnotherAsyncMiddleware())
        ctx = PipelineContext(agent)
        interrupted = await pipeline.run_round_async(ctx)
    """

    def __init__(self):
        self._async_middlewares: list[AsyncMiddleware] = []
        # ★ Bug4: 最近一次 run_round_async 的 PipelineContext 引用，
        #   供 session._emit_round_events 检查 checkpoint_requested 标记
        self._last_ctx: PipelineContext | None = None

    # ── 中间件管理（异步） ───────────────────────────────

    def use_async(self, middleware: AsyncMiddleware) -> Pipeline:
        """注册一个异步中间件（按注册顺序执行）

        Args:
            middleware: AsyncMiddleware 实例

        Returns:
            self（链式调用）
        """
        self._async_middlewares.append(middleware)
        return self

    @property
    def async_middlewares(self) -> list[AsyncMiddleware]:
        """当前异步中间件列表（只读视图）"""
        return list(self._async_middlewares)

    # ═════════════════════════════════════════════════════════
    # 异步执行路径
    # ═════════════════════════════════════════════════════════

    async def _get_interrupt_port(self, ctx: PipelineContext):
        """获取中断端口 — 优先使用 ctx.interrupt_port，回退到 agent._interrupt_port，最后使用默认适配器"""
        port = getattr(ctx, "interrupt_port", None)
        if port is None:
            agent = ctx.agent
            port = getattr(agent, '_interrupt_port', None)
        if port is None:
            from .adapters.interrupt import DefaultInterruptAdapter
            port = DefaultInterruptAdapter()
        return port

    async def run_round_async(self, ctx: PipelineContext) -> bool:
        """异步执行一轮对话，返回是否被中断

        驱动模型调用-工具执行循环，在每次迭代中触发中间件钩子：
            before_model_call → [async 模型调用] → after_model_call
            → 若有工具: before_tool → [async 工具执行] → after_tool
            → 若继续: 下一轮
            → 否则: round_complete
        """
        ctx.interrupted = False
        ctx.round_complete = False

        agent = ctx.agent
        session_id = getattr(ctx, "session_id", None)

        # ── turn/start：轮次打开（领取首条输入之前） ──
        await _event_facets.turn_start(agent, session_id=session_id)

        # ── agent/pre-step 前的自动上下文压缩 ──────────────
        # dsh compaction-basic 在 agent/pre-step 挂载压力压缩；本实现在同一
        # 时点驱动「所有 Agent 通用」的自动压缩检查（主 Agent 走 Pipeline，
        # SubAgent 在自己的模型调用前调用同一方法）。
        try:
            await asyncio.to_thread(agent.maybe_compact)
        except Exception:
            _logger.debug("pre-step 自动压缩检查失败", exc_info=True)

        # ── agent/pre-step（waterfall）：决定接纳的输入 ──
        try:
            decision = await _event_facets.pre_step(agent, agent.messages)
        except Exception:
            _logger.exception("agent/pre-step 切面异常，按接纳处理")
            decision = {"messages": agent.messages, "reject": False, "enter": True}
        if decision.get("reject") or decision.get("enter") is False:
            ctx.round_complete = True
            await _event_facets.turn_end(
                agent, interrupted=False, session_id=session_id,
                reason="pre-step rejected",
            )
            await self._fire_hooks_async('on_round_complete', ctx)
            self._last_ctx = ctx
            return ctx.interrupted

        step_index = 0
        while not ctx.round_complete and not ctx.interrupted:
            step_index += 1
            await _event_facets.step_start(agent, step_index, messages=agent.messages)
            # ── 每一步请求前的自动上下文压缩 ────────────────
            # dsh compaction-basic 在每次请求派生前检查压力（而非仅每轮一次），
            # 长工具循环中上下文随步骤增长，逐步检查可更早释放空间。
            try:
                await asyncio.to_thread(agent.maybe_compact)
            except Exception:
                _logger.debug("步骤前自动压缩检查失败", exc_info=True)
            try:
                # ── before_model_call ──────────────────────────
                await self._fire_hooks_async('before_model_call', ctx)

                if ctx.interrupted:
                    break

                # ── 核心：异步模型调用 ────────────────────────
                try:
                    await self._execute_model_call_async(ctx)
                except asyncio.CancelledError:
                    _logger.warning("Pipeline._execute_model_call_async 被取消，round 以 interrupted 结束")
                    ctx.interrupted = True
                    ctx.round_complete = True
                    ctx.error = asyncio.CancelledError("Pipeline 模型调用被取消")
                    # ★ Bug4: 标记 checkpoint 请求，让 session 在 _emit_round_events 中保存 checkpoint
                    ctx.checkpoint_requested = True
                    # 不 raise：让 round 以 interrupted 状态正常完成
                    # 后续 session._execute_round 仍会执行上下文压缩、保存、状态转换
                except Exception as e:
                    _logger.exception("Pipeline._execute_model_call_async 异常")
                    ctx.error = e
                    ctx.interrupted = True
                    await self._fire_on_exception_async(ctx, e)

                if ctx.interrupted:
                    break

                # ── after_model_call ───────────────────────────
                await self._fire_hooks_async('after_model_call', ctx)

                if ctx.interrupted:
                    break

                # ── 工具处理 ──────────────────────────────────
                if ctx.tool_calls:
                    await self._fire_hooks_async('before_tool_execution', ctx)

                    if ctx.interrupted:
                        break

                    # 执行工具（代理给 agent._handle_tool_calls）
                    # 确保中间件的 before_tool_execution 与 after_tool_execution
                    # 之间确实有真正的工具执行逻辑
                    agent = ctx.agent
                    if ctx.tool_calls and hasattr(agent, '_handle_tool_calls'):
                        try:
                            await agent._handle_tool_calls(
                                ctx.content, ctx.tool_calls, ctx.reasoning, ctx.usage,
                            )
                        except asyncio.CancelledError:
                            _logger.warning("工具执行期间被取消，round 以 interrupted 结束")
                            ctx.interrupted = True
                        except Exception as e:
                            _logger.exception("工具执行异常，round 以 interrupted 结束: %s", e)
                            ctx.error = e
                            ctx.interrupted = True
                        finally:
                            # P0-1: 先强制清空所有 active_labels，确保 cleanup() 恢复 sys.stdout 且无残留标签
                            cm = getattr(agent, '_capture_mgr', None)
                            if cm is not None:
                                try:
                                    if hasattr(cm, '_state') and cm._state is not None:
                                        labels = cm._state.get('active_labels', [])
                                        if labels:
                                            labels.clear()
                                        # 同时也清理 real_stdout 引用，确保不持有过时的 stdout 引用
                                        cm._state['real_stdout'] = sys.__stdout__
                                    cm.cleanup()
                                except Exception:
                                    _logger.debug("_capture_mgr.cleanup() 失败（非关键）")

                    await self._fire_hooks_async('after_tool_execution', ctx)

                    # 工具执行后检查中断信号，减少中断响应延迟
                    if not ctx.interrupted:
                        try:
                            interrupt_port = await self._get_interrupt_port(ctx)
                            if await interrupt_port.is_interrupted():
                                ctx.interrupted = True
                        except Exception:
                            _logger.debug("中断检查失败（非关键）")

                    # ── agent/turn-stopping（serial）：是否还有欠下的工作 ──
                    if not ctx.interrupted:
                        stop = await self._resolve_turn_stopping(
                            agent, ctx, step_index, default_stop=False,
                        )
                        if stop:
                            ctx.round_complete = True
                else:
                    # 无工具调用 → 默认停止；turn-stopping 可显式声明继续
                    stop = await self._resolve_turn_stopping(
                        agent, ctx, step_index, default_stop=True,
                    )
                    ctx.round_complete = stop
            finally:
                # ── step/end：本步骤结束（含中断路径） ─────────
                await _event_facets.step_end(
                    agent, step_index, tool_calls=ctx.tool_calls,
                    interrupted=ctx.interrupted,
                )

        # ── turn/end：轮次关闭 ────────────────────────────
        await _event_facets.turn_end(
            agent, interrupted=ctx.interrupted, session_id=session_id,
        )

        # ── on_round_complete ─────────────────────────────
        await self._fire_hooks_async('on_round_complete', ctx)

        # ★ Bug4: 保存 ctx 引用供 session 检查 checkpoint_requested 标记
        self._last_ctx = ctx

        return ctx.interrupted

    async def _resolve_turn_stopping(
        self, agent: Any, ctx: PipelineContext, step_index: int, *, default_stop: bool
    ) -> bool:
        """运行 ``agent/turn-stopping``（serial）并解析停止决策。

        监听器返回 ``{"stop": bool}`` 可覆盖默认决策；返回 bool 亦可；
        其它返回值（含 None）保持 ``default_stop``。
        """
        try:
            result = await _event_facets.turn_stopping(
                agent,
                {
                    "step_index": step_index,
                    "tool_calls": list(ctx.tool_calls or []),
                    "interrupted": ctx.interrupted,
                },
            )
        except Exception:
            _logger.exception("agent/turn-stopping 切面异常，使用默认决策")
            return default_stop
        if isinstance(result, dict) and "stop" in result:
            return bool(result["stop"])
        if isinstance(result, bool):
            return result
        return default_stop

    async def _execute_model_call_async(self, ctx: PipelineContext) -> None:
        """异步版核心模型调用（不含工具执行）

        执行一次模型调用，根据结果：
        - 中断 → 设置 ctx.interrupted
        - 有工具调用 → 仅设置 ctx.tool_calls，工具执行由 run_round_async 中的中间件钩子处理
        - 无工具调用 → 追加 assistant 消息
        """
        agent = ctx.agent

        # 异步模型调用
        # 使用 getattr 而非 hasattr：hasattr 在属性存在但设为 None 时返回 True，
        # 导致后续调用 None() 抛出 TypeError。getattr 的三参数形式安全区分
        # "属性不存在"和"属性存在但为 None"两种场景。
        call_model_async = getattr(agent, '_call_model_async', None)
        # 根据当前模式获取工具集（plan 模式只暴露只读工具）
        tools = agent._get_active_tools() if hasattr(agent, '_get_active_tools') else agent.tools
        if call_model_async is None:
            # agent._call_model_async 未设置，应无法到达此路径
            # （Agent.__init__ 始终设置 _call_model_async）。
            # 保留防御性异常以使 mock 测试可见。
            _logger.error("_call_model_async 未设置，跳过模型调用")
            ctx.interrupted = True
            return

        # ── agent/request（waterfall）：请求前解析路由、消息与工具 ──
        call = {
            "messages": agent.messages,
            "model": agent.model,
            "tools": tools,
            "display": agent.display,
            "label": "assistant",
        }
        call = await _event_facets.request(agent, call)

        def _make_invoke(current_call):
            async def _invoke_model():
                return await call_model_async(
                    current_call.get("messages", agent.messages),
                    model=current_call.get("model", agent.model),
                    tools=current_call.get("tools", tools),
                    display=current_call.get("display", agent.display),
                    label=current_call.get("label", "assistant"),
                )
            return _invoke_model

        # ── llm/stream（waterfall）：环绕流式模型调用 ──
        # 上下文窗口溢出（提供方确认）时压缩历史后重试该请求
        # （dsh compaction-basic 的 agent/request-error 恢复语义）。
        overflow_attempt = 0
        while True:
            try:
                reasoning, content, usage, tool_calls = await _event_facets.llm_stream(
                    agent, call, _make_invoke(call)
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if (overflow_attempt < _MAX_OVERFLOW_RETRIES
                        and is_context_overflow_error(None, exc)
                        and await self._recover_context_overflow(agent)):
                    overflow_attempt += 1
                    call = await self._reload_request_call(agent, call)
                    continue
                raise
            if (overflow_attempt < _MAX_OVERFLOW_RETRIES and not tool_calls
                    and is_context_overflow_error(content)
                    and await self._recover_context_overflow(agent)):
                overflow_attempt += 1
                call = await self._reload_request_call(agent, call)
                continue
            break

        ctx.model_calls += 1
        ctx.reasoning = reasoning
        ctx.content = content
        ctx.usage = usage
        ctx.tool_calls = tool_calls

        # 中断检查
        interrupt_port = await self._get_interrupt_port(ctx)
        if await interrupt_port.is_interrupted():
            agent._append_assistant_msg(_INTERRUPTED_MSG, reasoning)
            ctx.interrupted = True
            return
        # 工具调用 → 由 run_round_async 中的中间件钩子处理
        # 此处仅追加 assistant 消息，工具执行在 before/after_tool_execution 之间完成
        # 注意：仅当 content 非空时才追加，避免空 content 消息导致 API 兼容性问题
        if not tool_calls and content:
            agent._append_assistant_msg(content, reasoning)

    @staticmethod
    async def _recover_context_overflow(agent: Any) -> bool:
        """上下文溢出恢复：强制压缩历史，返回是否发生了缩减。"""
        recover = getattr(agent, "recover_context_overflow", None)
        if not callable(recover):
            return False
        try:
            return await asyncio.to_thread(recover)
        except Exception:
            _logger.debug("上下文溢出恢复失败", exc_info=True)
            return False

    @staticmethod
    async def _reload_request_call(agent: Any, call: dict) -> dict:
        """压缩后按最新消息重建请求（保留原路由/工具/展示）。"""
        reloaded = dict(call)
        reloaded["messages"] = agent.messages
        return await _event_facets.request(agent, reloaded)

    async def _fire_on_exception_async(self, ctx: PipelineContext, exc: Exception) -> None:
        """触发所有异步中间件的 on_exception 钩子

        单个中间件的 on_exception 异常不会阻止后续中间件的执行，
        确保所有中间件都有机会处理异常。
        """
        for mw in self._async_middlewares:
            try:
                await mw.on_exception(ctx, exc)
            except Exception:
                _logger.exception("中间件 %s.on_exception 异常", mw.name)
                # 继续执行下一个中间件，不阻断异常传播链

    async def _fire_hooks_async(self, hook_name: str, ctx: PipelineContext) -> None:
        """串行执行所有异步中间件的指定钩子方法（按注册顺序）。

        某些中间件依赖特定的执行顺序（如审计日志必须在状态变更前执行），
        串行执行保证按注册顺序运行，消除并行执行的竞态风险。
        单个中间件异常不会阻止后续中间件的执行。

        注意：中间件异常会设置 ctx.interrupted = True 阻止 round 继续执行。
        这是安全设计——中间件异常意味着系统状态不确定，继续执行可能造成
        更大的不一致。非关键中间件应自行在内部 try/except 保护。
        """
        for mw in self._async_middlewares:
            hook = getattr(mw, hook_name, None)
            if hook is not None:
                try:
                    await hook(ctx)
                except Exception as e:
                    _logger.error("中间件 %s 钩子 %s 异常: %s", mw.name, hook_name, e)
                    if not ctx.interrupted:
                        ctx.interrupted = True
                        ctx.error = e

    def __repr__(self) -> str:
        names = [mw.name for mw in self._async_middlewares]
        return f"<Pipeline: {' → '.join(names)}>"