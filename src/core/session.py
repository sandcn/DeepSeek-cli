"""ChatSession — 纯领域会话对象（生命周期编排）

职责范围：
1. 对话生命周期管理（session/round/retry）— 由 SessionStateMachine 形式化控制
2. Agent + ContextManager 编排
3. 通过 Hook 机制解耦 UI 层
4. 通过 Telemetry 提供可观测性数据

已提取的子模块：
- _session_persistence.py — 会话持久化（save/load/auto-save/checkpoint）
- _session_messages.py — 消息管理（add/filter）

设计原则：
- 零依赖 UI 层（无 ui. 包导入）
- 可测试：所有依赖可注入
- 可扩展：Hook 系统支持任意前端适配
- 状态形式化：SessionStateMachine 消除布尔状态组合
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import logging
from typing import AsyncIterator, Callable

from .agent import Agent
from .context_manager import ContextManager
from .state_machine import SessionStateMachine, SessionState, InvalidTransitionError
from .telemetry import get_default_collector, get_default_tracer
from .sandbox_manager import create_sandbox_manager, get_sandbox_manager
from .middleware.state_machine import StateMachineMiddleware
# 向后兼容保留 — 实际使用已改为方法体内延迟导入
# from ..api.stats import get_token_stats, get_session_start_time
from ..core.ports import PersistencePort, CheckpointPort, ConfigPort
from ..core.ports.observability import ObservabilityPort
from ..core.adapters.persistence import JsonFilePersistence, JsonFileCheckpoint
from ..core.adapters.config import DefaultConfigAdapter
from ..core.adapters.observability import DefaultObservabilityAdapter
from ..core.ports.null import _NullPort, _NullOutputPort  # noqa: F401 — re-exported
from .internal.session._session_persistence import (
    save_session as _save_session_legacy,
    load_session_data as _load_session_data_legacy,
    save_checkpoint_session, clear_checkpoint_session, load_checkpoint_data,
    has_checkpoint_session, resume_from_checkpoint_session, safe_save_state,
)
from .internal.session._session_messages import add_message, non_system_messages, system_messages
from .internal.session._session_state import SessionState as _SessionData
from .internal.session._session_compression import _validate_compress_preconditions
from .internal.session._session_persistence_manager import SessionPersistenceManager
from .internal.session._session_messaging_manager import SessionMessagingManager

_logger = logging.getLogger(__name__)

# ── 魔法数字常量 ─────────────────────────────────────
_LOG_TRUNCATE_LENGTH = 50           # 日志截断长度（run_round 排队消息日志）
_MAX_PENDING_LOOP_ITER = 10         # run_pending_loop 最大轮次熔断阈值
_MIN_NON_SYSTEM_FOR_COMPRESS = 2    # compress 中非 system 消息最小值

# ── 消息字典键常量 ─────────────────────────────────
_ROLE_KEY = "role"
_SYSTEM_ROLE = "system"

# ═══════════════════════════════════════════════════════════════
# ChatSession
# ═══════════════════════════════════════════════════════════════



class ChatSession:
    """纯领域会话对象 — 零 UI 依赖，状态机驱动。

    使用方式:
        session = ChatSession()
        session.on("round_end", lambda **kw: locked_print(kw))
        result = session.run_round("你好")
    """

    def __init__(self, *, model: str | None = None,
                 agent: Agent | None = None,
                 sandbox=None,
                 persistence_port: PersistencePort | None = None,
                 checkpoint_port: CheckpointPort | None = None,
                 config_port: ConfigPort | None = None,
                 observability_port: ObservabilityPort | None = None):
        # ── ObservablePort（可观测性，优先传入） ──────────
        if observability_port is not None:
            self._observability_port = observability_port
        else:
            self._observability_port = DefaultObservabilityAdapter()

        # ── 端口注入（默认适配器保持向后兼容） ──────────
        self._persistence_port = persistence_port or JsonFilePersistence()
        self._checkpoint_port = checkpoint_port or JsonFileCheckpoint()
        self._config_port = config_port or DefaultConfigAdapter()

        # ── 核心对象 ──────────────────────────────────────
        self._model: str = model or self._config_port.get_model()

        # 创建 Agent（注入 NullPort 避免 UI 依赖，传递 observability_port）
        null_display = _NullPort()
        null_event = _NullPort()
        null_output = _NullOutputPort()
        self._agent = agent or Agent(
            model=self._model,
            display_port=null_display,
            event_port=null_event,
            output_port=null_output,
            sandbox=sandbox,
            observability_port=self._observability_port,
        )

        # 上下文管理器（延迟初始化，需等待 messages 就绪）
        self._ctx_mgr: ContextManager | None = None

        # ── 状态容器（会话可变状态集中管理） ──────────────
        self._state = _SessionData()

        # ── 状态机（形式化会话生命周期） ──────────────────
        self._state_machine = SessionStateMachine()
        self._setup_state_machine_hooks()

        # ── 可观测性（向后兼容：self._metrics / self._tracer 仍可用） ──
        self._metrics = get_default_collector()
        self._tracer = get_default_tracer()

        # ── 持久化管理器 ─────────────────────────────────
        self._persistence_mgr = SessionPersistenceManager(
            messages_getter=lambda: self._agent.messages,
            model_getter=lambda: self._model,
            model_setter=lambda v: setattr(self, '_model', v),
            session_id_getter=lambda: self._state.session_id,
            session_id_setter=lambda v: setattr(self._state, 'session_id', v),
            persistence_port=self._persistence_port,
            checkpoint_port=self._checkpoint_port,
            state_machine=self._state_machine,
            emit_fn=self._emit,
            observability_port=self._observability_port,
        )

        # ── 消息管理器（延迟初始化，等待 messages 和 ctx_mgr 就绪） ──
        self._msg_mgr: SessionMessagingManager | None = None

    def _init_msg_mgr(self) -> SessionMessagingManager:
        """惰性初始化消息管理器（需等待 ctx_mgr 就绪）。"""
        if self._msg_mgr is None:
            self._msg_mgr = SessionMessagingManager(
                messages=self._agent.messages,
                model_getter=lambda: self._model,
                context_manager_getter=lambda: self._ctx_mgr,
                context_manager_setter=lambda v: setattr(self, '_ctx_mgr', v),
                sandbox_getter=get_sandbox_manager,
                state_machine=self._state_machine,
                emit_fn=self._emit,
                observability_port=self._observability_port,
                retry_pending_getter=lambda: self._state.retry_pending,
                retry_pending_setter=lambda v: setattr(self._state, 'retry_pending', v),
            )
        return self._msg_mgr

    def _setup_state_machine_hooks(self) -> None:
        """设置状态机转换回调（用于可观测性等）"""
        def _on_enter_running(old, new, **kw):
            self._observability_port.counter("session.rounds", 1)

        def _on_enter_interrupted(old, new, **kw):
            self._observability_port.counter("session.interrupts", 1)

        def _on_enter_completed(old, new, **kw):
            # 记录消息数到仪表盘
            self._observability_port.gauge("session.messages", len(self._agent.messages))

        self._state_machine.on_enter(SessionState.RUNNING, _on_enter_running)
        self._state_machine.on_enter(SessionState.INTERRUPTED, _on_enter_interrupted)
        self._state_machine.on_enter(SessionState.COMPLETED, _on_enter_completed)

    # ── 属性 ──────────────────────────────────────────────

    @property
    def messages(self) -> list[dict]:
        """当前消息列表（直连 Agent.messages，就地修改）。"""
        return self._agent.messages

    @property
    def model(self) -> str:
        return self._model

    @model.setter
    def model(self, value: str) -> None:
        self._model = value
        self._agent.model = value
        if self._ctx_mgr:
            self._ctx_mgr.update_model(value)

    @property
    def session_id(self) -> str | None:
        return self._state.session_id

    @session_id.setter
    def session_id(self, value: str | None) -> None:
        self._state.session_id = value

    @property
    def agent(self) -> Agent:
        return self._agent

    @property
    def context_manager(self) -> ContextManager | None:
        return self._ctx_mgr

    @property
    def retry_pending(self) -> bool:
        """是否有待重试的回合（最后一条消息是 user 且尚未回复）"""
        return self._state.retry_pending

    def sync_retry_pending(self) -> None:
        """★ Bug#2 修复：根据最后一条消息的角色同步 retry_pending 标志。

        编辑/截断消息后调用此方法，确保 retry_pending 与 messages 的实际状态一致：
        - 最后一条是 user → retry_pending = True（待自动续接）
        - 否则 → retry_pending = False
        """
        self._state.retry_pending = (
            len(self._agent.messages) > 0
            and self._agent.messages[-1].get(_ROLE_KEY) == "user"
        )

    def _safe_save_state(self) -> None:
        """安全执行状态机 save 转换（忽略无效转换）。"""
        self._persistence_mgr._safe_save_state()

    @property
    def pending_messages(self) -> list[str]:
        """当前排队的用户消息列表（只读视图）"""
        return list(self._state.pending_messages)

    def pop_pending_messages(self) -> list[str]:
        """弹出并返回所有排队的用户消息。

        run_round 在 RUNNING 状态下被重复调用时，消息会被暂存到
        pending_messages 队列中。调用者应在每轮 run_round 完成后
        检查并处理这些排队消息。
        """
        return self._state.pop_pending_messages()

    @property
    def state_machine(self) -> SessionStateMachine:
        """会话状态机（只读访问）"""
        return self._state_machine

    @property
    def state_name(self) -> str:
        """当前状态的字符串名称"""
        return self._state_machine.name

    @property
    def captured_prefill(self) -> str:
        """获取 LLM 生成期间用户键入的捕获文本"""
        return self._state.captured_prefill

    @captured_prefill.setter
    def captured_prefill(self, value: str) -> None:
        """设置 LLM 生成期间用户键入的捕获文本"""
        self._state.captured_prefill = value

    @property
    def _non_system_messages(self) -> list[dict]:
        return non_system_messages(self)

    @property
    def _system_messages(self) -> list[dict]:
        return system_messages(self)

    # ── Hook 系统 ─────────────────────────────────────────

    def on(self, event: str, callback: Callable) -> None:
        """注册事件回调。

        支持的事件:
            round_start     — 一轮对话开始
            round_end       — 一轮对话完成
            cost_update     — 有 token 消耗可显示
            saved           — 会话已保存
            loaded          — 会话已加载
            checkpoint_saved    — 断点已保存
            checkpoint_cleared  — 断点已清除
            messages_changed    — 消息列表被外部修改
        """
        self._state.hooks.on(event, callback)

    def off(self, event: str, callback: Callable) -> None:
        """移除事件回调。"""
        self._state.hooks.off(event, callback)

    def _emit(self, event: str, **data) -> None:
        """触发事件，依次调用所有注册的回调。"""
        self._state.hooks._emit(event, **data)

    # ── 初始化 ────────────────────────────────────────────

    def initialize(self, model: str | None = None,
                   loaded_messages: list[dict] | None = None) -> None:
        """初始化会话上下文。

        首次使用或重置时调用。创建 SandboxManager + ContextManager，
        可选加载历史消息。
        状态转换: INIT → IDLE

        Args:
            model: 模型名称，None 使用当前值
            loaded_messages: 历史消息列表（不含 system 消息）
        """
        # ★ Bug5 修复：仅在沙盒管理器未创建时才创建，避免
        #   _force_state_recovery 后重新初始化时覆盖已有沙盒状态
        if get_sandbox_manager() is None:
            create_sandbox_manager()
        sandbox = get_sandbox_manager()

        if model:
            self._model = model
            self._agent.model = model

        # 创建 ContextManager
        def _sandbox_callback(event):
            sm = get_sandbox_manager()
            if not sm:
                return
            if event["type"] == "insert":
                sm.shift_indices(event["index"])
            elif event["type"] == "remove":
                sm.remap_indices(event["indices"])

        self._ctx_mgr = ContextManager(
            messages=self._agent.messages,
            model=self._model,
            on_messages_changed=_sandbox_callback,
        )
        self._agent.context_manager = self._ctx_mgr

        # 加载历史消息
        if loaded_messages:
            for msg in loaded_messages:
                self._agent.messages.append(msg)
            self._emit("loaded", data={
                "messages": loaded_messages,
                "model": self._model,
            })

        # 状态转换: INIT → IDLE
        try:
            self._state_machine.initialize()
        except InvalidTransitionError:
            # 已经初始化过则跳过
            pass

        # 初始化可观测性
        self._observability_port.gauge("session.messages", len(self._agent.messages))

        # ── 注册状态机 Pipeline 中间件 ──────────────────
        # ★ P1 修复: isinstance(mw, type) 对实例永远为 False，
        #   改为检查类名，正确防止重复注册。
        if not any(mw.__class__.__name__ == 'StateMachineMiddleware'
                   for mw in self._agent.pipeline.async_middlewares):
            self._agent.pipeline.use_async(StateMachineMiddleware())

    # ── 核心对话方法 ──────────────────────────────────────

    def _ensure_idle(self) -> None:
        """确保状态机不在 INIT 状态（兼容未调用 initialize 的场景）"""
        if self._state_machine.is_(SessionState.INIT):
            try:
                self._state_machine.initialize()
            except InvalidTransitionError:
                pass

    # ═════════════════════════════════════════════════════════
    # 异步核心对话方法
    # ═════════════════════════════════════════════════════════

    def _force_state_recovery(self) -> None:
        """异常后强制恢复状态机离开当前状态回到 IDLE。

        当 _execute_round 在状态转换代码执行前/后抛异常时，
        状态机可能残留在 RUNNING/COMPLETED/INTERRUPTED。
        此方法尝试依次使用多个方案恢复，确保后续 run_round 能正常处理消息。
        """
        current_state = self._state_machine.name
        if self._state_machine.is_(SessionState.IDLE) or self._state_machine.is_(SessionState.INIT):
            return
        _logger.warning("状态机残留在 %s，执行强制恢复", current_state)
        # 尝试多种转换路径回到 IDLE
        for method_name in ['complete_round', 'interrupt', 'clear', 'save', 'reset']:
            method = getattr(self._state_machine, method_name, None)
            if method is None:
                _logger.warning("强制恢复: 方法 %s 不存在于状态机", method_name)
                continue
            try:
                method()
                _logger.info("强制恢复成功: %s()", method_name)
                return
            except InvalidTransitionError:
                _logger.debug("强制恢复: %s() 转换无效，尝试下一个", method_name)
                continue
        # 保底
        _logger.error("所有状态恢复方案均失败，执行 reset 到 INIT")
        self._state_machine.reset()
        self._emit("state_recovered", old_state=current_state)

    @contextlib.asynccontextmanager
    async def _handle_round_error(self, label: str) -> AsyncIterator[None]:
        """统一处理 _execute_round 异常的上下文管理器。

        提取 run_round / retry / run_single 中重复的异常处理模式：
        - 记录异常日志
        - 强制恢复状态机

        各方法的个性化清理在 yield 返回后的 except 块中完成。
        """
        try:
            yield
        except (KeyboardInterrupt, asyncio.CancelledError):
            raise
        except Exception:
            _logger.exception("%s: _execute_round 异常", label)
            try:
                self._force_state_recovery()
            except Exception as recovery_exc:
                _logger.exception("_force_state_recovery 在异常处理中再次失败: %s", recovery_exc)
            raise

    async def run_round(self, user_input: str) -> dict:
        """添加用户消息并执行一轮对话。

        变更行为：
        - 已在执行中时消息排队（Bug 3）：若状态机为 RUNNING，则将消息暂存到
          pending_messages 队列，返回 {"pending": True} 不阻塞当前轮次。
        - 异常回滚保护 AI 内容（Bug 2）：_execute_round 已部分执行（最后一条消息
          为 assistant）时，跳过 pop user 消息，保留 AI 已生成的内容供 retry 恢复。
        - 回滚后同步 context_manager 缓存（Bug 6）：pop user 消息或保留 AI 内容后，
          调用 context_manager.invalidate_cache() 确保缓存与消息列表一致。

        Args:
            user_input: 用户输入文本

        Returns:
            {"interrupted": bool, "session_id": str|None,
             "delta": dict, "pending": bool}
             pending=True 表示消息已排队（上一轮尚在执行中）。
        """
        async with self._state.round_lock:
            # ★ Bug3 修复：已在执行中则排队消息，不影响当前轮次
            if self._state_machine.is_(SessionState.RUNNING):
                self._state.pending_messages.append(user_input)
                _logger.warning("run_round 被重复调用，消息已排队 (#%d): %s...",
                                len(self._state.pending_messages), user_input[:_LOG_TRUNCATE_LENGTH])
                return {"interrupted": False, "session_id": None,
                        "delta": {"input": 0, "output": 0, "calls": 0},
                        "pending": True}
            self._ensure_idle()
            self._state_machine.start_round()
            try:
                self._agent.add_user_message(user_input)
            except (KeyboardInterrupt, asyncio.CancelledError):
                raise
            except Exception:
                _logger.exception("run_round: add_user_message 异常")
                self._force_state_recovery()
                raise
            # ── AI 回复前提前分配 session_id ──────────────────
            # 标题生成在后台并行执行，需要 session_id 已就绪才能保存标题。
            if not self._state.session_id:
                self._state.session_id = self._persistence_port.generate_id()
            try:
                async with self._handle_round_error("run_round"):
                    return await self._execute_round()
            except (KeyboardInterrupt, asyncio.CancelledError):
                raise
            except Exception:
                # 个性化清理（_handle_round_error 已记录日志并恢复状态机）
                self._rollback_round_on_error()
                raise

    def _rollback_round_on_error(self) -> None:
        """run_round 异常回滚的统一清理逻辑。

        提取自 run_round 的 except Exception 块（Bug 2 + Bug 6 修复），
        在 _handle_round_error 已记录日志并恢复状态机后执行个性化的消息和缓存清理。
        """
        # 回滚 orphan ID
        self._state.session_id = None

        # ★ Bug2：异常回滚时保护 AI 已生成的内容
        #   检查最后一条消息的角色——若为 assistant 说明 _execute_round
        #   已部分执行（AI 已生成回复），此时保留 AI 内容和对应的 user 消息；
        #   若仍为 user 说明 _execute_round 未开始，回滚该 user 消息。
        last_role = self._agent.messages[-1].get(_ROLE_KEY) if self._agent.messages else None
        if last_role == "assistant":
            # _execute_round 已部分执行，AI 已生成回复
            # 跳过 pop user 消息，保留 AI 已生成的内容供 retry 机制恢复
            _logger.warning(
                "run_round 异常，_execute_round 已部分执行（最后消息为 assistant），"
                "保留 AI 内容，待 retry 机制恢复"
            )
            # ★ Bug6：assistant 分支虽然没有 pop，但消息已变更，
            # 缓存可能不准确，也 invalidate 确保下次访问时重建
            if self._ctx_mgr is not None:
                self._ctx_mgr.invalidate_cache()
        elif last_role == "user":
            # _execute_round 未开始，回滚已添加的 user 消息
            pop_index = len(self._agent.messages) - 1
            self._agent.messages.pop()
            _logger.warning("run_round 异常，已回滚最后一条 user 消息")
            # ★ Bug6：回滚后同步 context_manager 状态
            if self._ctx_mgr is not None:
                self._ctx_mgr.invalidate_cache()
                self._ctx_mgr.notify_messages_removed([pop_index])
        else:
            # 其他情况（消息列表为空等），也 invalidate 缓存确保一致性
            if self._ctx_mgr is not None:
                self._ctx_mgr.invalidate_cache()

        # 清理已排队的消息，防止异常后残留无效数据
        self._state.pending_messages.clear()

    async def run_pending_loop(self, max_iter: int = _MAX_PENDING_LOOP_ITER) -> tuple[bool, list[str]]:
        """处理 run_round 执行期间产生的所有排队消息。

        将 _pending_messages 中的所有消息串行调用 run_round 处理，
        每处理完一轮后再次检查是否有新排队的消息，直到全部处理完毕或达到熔断阈值。

        CLI 和 WebUI 共用此方法，消除两端重复的排队消息处理逻辑。

        变更行为：
        - 增量 checkpoint（Bug 3）：每成功处理一条排队消息后立即调用 save_checkpoint()
          保存增量 checkpoint，确保中途异常时不丢失已成功处理的消息。

        Args:
            max_iter: 最大轮次阈值，防止无限循环（默认 10）

        Returns:
            (breached, unprocessed)
            - breached: 是否触发熔断（True 表示超过 max_iter 轮仍未处理完毕）
            - unprocessed: 熔断时残留的未处理消息列表（已重新放回 _pending_messages）
        """
        pending = self.pop_pending_messages()
        if not pending:
            return False, []

        total_count = 0
        while pending and total_count < max_iter:
            total_count += len(pending)
            for i, msg in enumerate(pending):
                try:
                    await self.run_round(msg)
                except (KeyboardInterrupt, asyncio.CancelledError):
                    raise
                except Exception:
                    remaining = pending[i + 1:]
                    if remaining:
                        self._state.pending_messages = remaining + self._state.pending_messages
                        _logger.error("排队消息处理异常，剩余 %d 条已重新入队", len(remaining))
                    raise
                else:
                    # ★ Bug3 修复：每成功处理一条排队消息，立即保存增量 checkpoint
                    try:
                        self.save_checkpoint()
                    except Exception:
                        _logger.exception("run_pending_loop: save_checkpoint 异常，不阻断消息处理")
            pending = self.pop_pending_messages()

        if total_count >= max_iter:
            _logger.error("排队消息处理超过熔断阈值 (%d)，终止循环", max_iter)
            remaining = self.pop_pending_messages()
            if remaining:
                self._state.pending_messages = remaining + self._state.pending_messages
            return True, list(self._state.pending_messages)

        return False, []

    async def retry(self) -> dict:
        """重新执行上一轮对话。
        """
        async with self._state.round_lock:
            self._state.retry_pending = False
            if self._state_machine.is_(SessionState.INIT):
                self._ensure_idle()
            self._state_machine.retry()
            try:
                async with self._handle_round_error("retry"):
                    return await self._execute_round()
            except (KeyboardInterrupt, asyncio.CancelledError):
                raise
            except Exception:
                # 个性化清理（_handle_round_error 已记录日志并恢复状态机）
                self._state.retry_pending = False
                raise

    async def run_single(self, prompt: str) -> dict:
        """单次对话模式。
        """
        async with self._state.round_lock:
            self._ensure_idle()
            self._agent.add_user_message(prompt)
            # ── AI 回复前提前分配 session_id ──────────────────
            if not self._state.session_id:
                self._state.session_id = self._persistence_port.generate_id()
            async with self._handle_round_error("run_single"):
                result = await self._execute_round()
            self.save()
            return result

    # ── _execute_round 子方法 ───────────────────────────

    def _prepare_round(self) -> None:
        """更新 agent 和 context_manager 的模型配置。"""
        self._agent.model = self._model
        if self._ctx_mgr:
            self._ctx_mgr.update_model(self._model)

    def _snapshot_token_stats(self) -> tuple[int, int, int]:
        """获取前置 token 统计快照，返回 (prev_input, prev_output, prev_calls)。"""
        from ..api.stats import get_token_stats
        current = get_token_stats()
        return current["input"], current["output"], current["calls"]

    async def _finalize_round(self, interrupted: bool,
                              prev_stats: tuple[int, int, int]) -> dict:
        """后置处理: enforce_message_limit → 计算 delta → 状态转换 → 自动保存 → 发射事件。

        Args:
            interrupted: agent.run() 是否被中断
            prev_stats: _snapshot_token_stats() 返回的前置快照

        Returns:
            结果字典（含 interrupted/session_id/delta/elapsed）
        """
        prev_input, prev_output, prev_calls = prev_stats

        # 消息限制（用 try 保护，确保即使异常也能执行后续状态转换）
        try:
            if self._ctx_mgr:
                self._ctx_mgr.enforce_message_limit()
        except Exception as exc:
            _logger.exception("enforce_message_limit 异常: %s", exc)

        # 计算本轮消耗
        delta, current = self._compute_token_delta(prev_input, prev_output, prev_calls)

        # 状态转换（由 StateMachineMiddleware 自动完成，此处为兜底）
        if self._state_machine.is_(SessionState.RUNNING):
            try:
                if interrupted:
                    self._state_machine.interrupt()
                else:
                    self._state_machine.complete_round()
            except InvalidTransitionError:
                _logger.warning("异步状态转换失败: %s → %s，执行强制恢复",
                                self._state_machine.name,
                                "interrupt" if interrupted else "complete")
                self._force_state_recovery()
            except Exception as exc:
                _logger.exception("状态转换异常，执行强制恢复: %s", exc)
                self._force_state_recovery()

        # 自动保存
        session_id = await self._auto_save()

        # 发射事件并返回
        return self._emit_round_events(interrupted, session_id, delta, current)

    async def _execute_round(self) -> dict:
        """执行一轮对话的公共逻辑（编排方法）。"""
        self._prepare_round()
        prev_stats = self._snapshot_token_stats()
        self._emit("round_start")
        interrupted: bool = await self._agent.run()
        return await self._finalize_round(interrupted, prev_stats)

    def _compute_token_delta(self, prev_input: int, prev_output: int, prev_calls: int) -> tuple[dict, dict]:
        """计算本轮 token 消耗增量，返回 (delta, current_stats)。"""
        from ..api.stats import get_token_stats
        current = get_token_stats()
        delta = {
            "input": current["input"] - prev_input,
            "output": current["output"] - prev_output,
            "calls": current["calls"] - prev_calls,
        }
        return delta, current

    async def _auto_save(self) -> str | None:
        """自动保存会话，返回 session_id（无可保存内容时返回 None）。"""
        try:
            return await self._persistence_mgr.auto_save(
                lambda: self._agent.messages,
            )
        except Exception as exc:
            _logger.exception("自动保存会话失败: %s", exc)
            self._force_state_recovery()
            return None

    def _emit_round_events(self, interrupted: bool, session_id: str | None,
                           delta: dict, current: dict) -> dict:
        """发射 round 事件，返回结果字典。

        变更行为：
        - Pipeline CancelledError 时额外保存 checkpoint（Bug 4）：检查 pipeline
          的 ctx.checkpoint_requested 标记，若为 True 则在已有 save_checkpoint()
          之后再次调用 save_checkpoint()，确保被取消的模型调用状态也被持久化。
        """
        from ..api.stats import get_session_start_time
        elapsed = time.time() - get_session_start_time()
        if delta["input"] > 0 or delta["output"] > 0:
            prices = self._config_port.get_token_prices()
            self._emit("cost_update",
                       delta=delta,
                       total=current,
                       model=self._model,
                       prices=prices,
                       session_elapsed=elapsed,
                       messages=self._agent.messages)

        if interrupted:
            # ★ Bug4: 检查 Pipeline 的 checkpoint_requested 标记
            #   （CancelledError 路径设置的），避免重复调用 save_checkpoint()
            # TODO: 通过 pipeline._last_ctx（私有属性）跨模块访问 checkpoin_requested
            #       是设计上的耦合。后续应考虑通过 Event/Callback 机制通知 session，
            #       或将 checkpoint_requested 合并到 round_end 事件的参数中传递。
            pipe_ctx = getattr(self._agent.pipeline, '_last_ctx', None)
            try:
                if pipe_ctx is not None and pipe_ctx.checkpoint_requested:
                    _logger.warning("Pipeline CancelledError 标记已检测，保存 checkpoint")
                    self.save_checkpoint()
                else:
                    self.save_checkpoint()
            except Exception as exc:
                _logger.warning("save_checkpoint 失败，不阻断事件发射: %s", exc)
            self._emit("interrupted")

        self._emit("round_end",
                   interrupted=interrupted,
                   session_id=session_id,
                   delta=delta,
                   elapsed=elapsed)

        # ★ 修复：每轮执行完成后复位 retry_pending，避免应用层误判需要自动续接
        self._state.retry_pending = False

        return {
            "interrupted": interrupted,
            "session_id": session_id,
            "delta": delta,
            "elapsed": elapsed,
        }

    # ── 消息管理（委托给 SessionMessagingManager） ──

    def add_user_message(self, content: str) -> None:
        """追加用户消息。"""
        return add_message(self, content)

    def clear_messages(self) -> int:
        """清空对话（保留 system prompt）。

        Returns:
            被删除的消息数量
        """
        try:
            self._state_machine.clear()
        except InvalidTransitionError:
            pass

        mgr = self._init_msg_mgr()
        return mgr.clear_messages(
            system_messages_fn=lambda: self._system_messages,
            build_system_prompt_fn=lambda: self._agent.build_system_prompt(),
        )

    def undo_last_round(self) -> int:
        """撤销上一轮对话。"""
        mgr = self._init_msg_mgr()
        return mgr.undo_last_round()

    def add_system_message(self, content: str) -> None:
        """追加系统消息。"""
        mgr = self._init_msg_mgr()
        mgr.add_system_message(content)

    def compress(self, force: bool = True) -> None:
        """手动执行上下文压缩（同步版本，会阻塞事件循环）。"""
        if not _validate_compress_preconditions(
            self._ctx_mgr, self._agent.messages, _MIN_NON_SYSTEM_FOR_COMPRESS
        ):
            return
        self._ctx_mgr.check_and_compress(force=force)

    async def async_compress(self, force: bool = True) -> None:
        """异步执行上下文压缩（推荐使用）。"""
        if not _validate_compress_preconditions(
            self._ctx_mgr, self._agent.messages, _MIN_NON_SYSTEM_FOR_COMPRESS
        ):
            return
        await asyncio.to_thread(self._ctx_mgr.check_and_compress, force=force)

    # ── 会话持久化（委托给 SessionPersistenceManager） ──

    def save(self) -> str | None:
        """保存当前会话到 .chat/msg_list/。"""
        return self._persistence_mgr.save()

    def load(self, session_id: str) -> dict | None:
        """加载历史会话。"""
        return self._persistence_mgr.load(session_id)

    def list_sessions(self) -> list[dict]:
        """列出所有保存的会话。"""
        return self._persistence_mgr.list_sessions()

    def get_session_ids(self) -> list[str]:
        """列出所有保存的会话 ID。"""
        return self._persistence_mgr.get_session_ids()

    # ── 断点管理（委托给 SessionPersistenceManager） ──

    def save_checkpoint(self) -> None:
        """保存断点（任务中断时调用）。"""
        self._persistence_mgr.save_checkpoint()

    def clear_checkpoint(self) -> None:
        """清除断点（任务成功完成时调用）。"""
        self._persistence_mgr.clear_checkpoint()

    def load_checkpoint(self) -> dict | None:
        """加载断点数据。"""
        return self._persistence_mgr.load_checkpoint()

    def has_checkpoint(self) -> bool:
        """检查是否存在有效断点。"""
        return self._persistence_mgr.has_checkpoint()

    def resume_from_checkpoint(self) -> bool:
        """从断点恢复任务。"""
        return self._persistence_mgr.resume_from_checkpoint(
            self._agent.messages, self._model,
            lambda v: setattr(self, '_model', v),
        )

    # ── 工具辅助 ──────────────────────────────────────────

    def get_prices(self) -> dict:
        """获取当前模型的价格配置。"""
        prices = self._config_port.get_token_prices().get(self._model)
        if not prices:
            all_prices = self._config_port.get_token_prices()
            prices = next(iter(all_prices.values())) if all_prices else {"input": 0.01, "output": 0.03}
        return prices

    # ── 可观测性接口 ──────────────────────────────────────

    def get_metrics_report(self) -> str:
        """获取当前会话的指标报告"""
        return self._metrics.report()

    def get_trace_report(self) -> str:
        """获取当前会话的追踪报告"""
        return self._tracer.report()
