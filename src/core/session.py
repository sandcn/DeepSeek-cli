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
import copy
import time
import logging
from typing import AsyncIterator, Callable

from .agent import Agent
from .context_manager import ContextManager
from .state_machine import SessionStateMachine, SessionState, InvalidTransitionError
from .telemetry import get_default_collector, get_default_tracer
from .sandbox_manager import create_sandbox_manager, get_sandbox_manager
from .middleware.state_machine import StateMachineMiddleware
from ..core.ports.stats import DefaultStatsAdapter
from ..core.ports import (
    PersistencePort, CheckpointPort, ConfigPort,
    JsonFilePersistence, JsonFileCheckpoint, DefaultConfigAdapter,
)
from ..core.ports.null import _NullPort, _NullOutputPort  # noqa: F401 — re-exported
from ._session_persistence import (
    save_session, load_session_data, list_sessions_fn, get_session_ids_fn,
    save_checkpoint_session, clear_checkpoint_session, load_checkpoint_data,
    has_checkpoint_session, resume_from_checkpoint_session, safe_save_state,
)
from ._session_messages import add_message, non_system_messages, system_messages
from ._session_state import SessionState as _SessionData
from ._round_engine import _RoundEngine

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
                 stats_port=None):
        # ── 端口注入（默认适配器保持向后兼容） ──────────
        self._persistence_port = persistence_port or JsonFilePersistence()
        self._checkpoint_port = checkpoint_port or JsonFileCheckpoint()
        self._config_port = config_port or DefaultConfigAdapter()

        # ── 统计端口 ─────────────────────────────────────
        if stats_port is not None:
            self._stats_port = stats_port
        else:
            from ..core.ports.stats import DefaultStatsAdapter
            self._stats_port = DefaultStatsAdapter()

        # ── 核心对象 ──────────────────────────────────────
        self._model: str = model or self._config_port.get_model()

        # 创建 Agent（注入 NullPort 避免 UI 依赖）
        null_display = _NullPort()
        null_event = _NullPort()
        null_output = _NullOutputPort()
        self._agent = agent or Agent(
            model=self._model,
            display_port=null_display,
            event_port=null_event,
            output_port=null_output,
            sandbox=sandbox,
        )

        # 上下文管理器（延迟初始化，需等待 messages 就绪）
        self._ctx_mgr: ContextManager | None = None

        # ── 状态容器（会话可变状态集中管理） ──────────────
        self._state = _SessionData()

        # ── 状态机（形式化会话生命周期） ──────────────────
        self._state_machine = SessionStateMachine()
        self._setup_state_machine_hooks()

        # ── 可观测性 ──────────────────────────────────────
        self._metrics = get_default_collector()
        self._tracer = get_default_tracer()
        # ★ P2 修复：显式初始化 _session_id_newly_allocated
        self._session_id_newly_allocated = False

        # ── 轮次执行引擎（从 session.py 提取的 round 生命周期） ──
        self._round_engine = _RoundEngine(self)


    def _setup_state_machine_hooks(self) -> None:
        """设置状态机转换回调（用于可观测性等）"""
        def _on_enter_running(old, new, **kw):
            self._metrics.counter("session.rounds", 1)

        def _on_enter_interrupted(old, new, **kw):
            self._metrics.counter("session.interrupts", 1)

        def _on_enter_completed(old, new, **kw):
            # 记录消息数到仪表盘
            self._metrics.gauge("session.messages", len(self._agent.messages))

        self._state_machine.on_enter(SessionState.RUNNING, _on_enter_running)
        self._state_machine.on_enter(SessionState.INTERRUPTED, _on_enter_interrupted)
        self._state_machine.on_enter(SessionState.COMPLETED, _on_enter_completed)

    # ── 属性 ──────────────────────────────────────────────

    @property
    def messages(self) -> list[dict]:
        """当前消息列表的只读副本（防止外部就地修改）。"""
        return list(self._agent.messages)

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
        return safe_save_state(self)

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
        self._state.on(event, callback)

    def off(self, event: str, callback: Callable) -> None:
        """移除事件回调。"""
        self._state.off(event, callback)

    def _emit(self, event: str, **data) -> None:
        """触发事件，依次调用所有注册的回调。"""
        self._state._emit(event, **data)

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

        # ★ Bug I 修复：避免重复 initialize 覆盖已有的 ContextManager
        if self._ctx_mgr is None:
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
        self._metrics.gauge("session.messages", len(self._agent.messages))

        # ── 注册状态机 Pipeline 中间件 ──────────────────
        # ★ P1 修复: isinstance(mw, type) 对实例永远为 False，
        #   改为检查类名，正确防止重复注册。
        if not any(mw.__class__.__name__ == 'StateMachineMiddleware'
                   for mw in self._agent.pipeline.async_middlewares):
            self._agent.pipeline.use_async(StateMachineMiddleware())
        self._agent._session_state_machine = self._state_machine

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

    def force_state_recovery(self) -> None:
        """公开的强制状态恢复方法（供 app_loop.py 等外部模块使用）。

        委托给 _force_state_recovery()，将内部实现暴露为公开接口。
        仅供异常恢复场景使用，正常流程不应调用。
        """
        self._force_state_recovery()

    @contextlib.asynccontextmanager
    async def _handle_round_error(self, label: str) -> AsyncIterator[None]:
        """统一处理 _execute_round 异常的上下文管理器。委托给 _RoundEngine。"""
        async with self._round_engine.handle_round_error(label):
            yield

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
            self._session_id_newly_allocated = not self._state.session_id
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
        """run_round 异常回滚的统一清理逻辑。委托给 _RoundEngine。"""
        self._round_engine.rollback_round_on_error()

    async def run_pending_loop(self, max_iter: int = _MAX_PENDING_LOOP_ITER) -> tuple[bool, list[str]]:
        """处理 run_round 执行期间产生的所有排队消息。

        将 _pending_messages 中的所有消息串行调用 run_round 处理，
        每处理完一轮后再次检查是否有新排队的消息，直到全部处理完毕或达到熔断阈值。

        CLI 和 WebUI 共用此方法，消除两端重复的排队消息处理逻辑。

        变更行为：
        - 增量 checkpoint（Bug 3）：每成功处理一条排队消息后立即调用 save_checkpoint()
          保存增量 checkpoint，确保中途异常时不丢失已成功处理的消息。
        - P2 修复：在 pop_pending_messages 和 _state.pending_messages 访问处添加
          round_lock 保护，确保与 run_round 的并发安全。

        Args:
            max_iter: 最大轮次阈值，防止无限循环（默认 10）

        Returns:
            (breached, unprocessed)
            - breached: 是否触发熔断（True 表示超过 max_iter 轮仍未处理完毕）
            - unprocessed: 熔断时残留的未处理消息列表（已重新放回 _pending_messages）
        """
        async with self._state.round_lock:
            pending = self.pop_pending_messages()
        if not pending:
            return False, []

        round_count = 0
        while pending and round_count < max_iter:
            for i, msg in enumerate(pending):
                try:
                    await self.run_round(msg)
                except (KeyboardInterrupt, asyncio.CancelledError):
                    raise
                except Exception:
                    async with self._state.round_lock:
                        remaining = pending[i + 1:]
                        if remaining:
                            self._state.pending_messages = remaining + self._state.pending_messages
                            _logger.error("排队消息处理异常，剩余 %d 条已重新入队", len(remaining))
                    raise
                else:
                    # ★ Bug3 修复：每成功处理一条排队消息，立即保存增量 checkpoint
                    try:
                        await self.save_checkpoint()
                    except Exception:
                        _logger.exception("run_pending_loop: save_checkpoint 异常，不阻断消息处理")
            async with self._state.round_lock:
                pending = self.pop_pending_messages()
            round_count += 1

        if round_count >= max_iter:
            _logger.error("排队消息处理超过熔断阈值 (%d)，终止循环", max_iter)
            async with self._state.round_lock:
                remaining = self.pop_pending_messages()
                if remaining:
                    self._state.pending_messages = remaining + self._state.pending_messages
                remaining_snapshot = list(self._state.pending_messages)
            return True, remaining_snapshot

        return False, []

    async def retry(self) -> dict:
        """重新执行上一轮对话。
        """
        async with self._state.round_lock:
            # ★ Bug H 修复：验证 retry_pending，无待重试消息时拒绝
            if not self._state.retry_pending:
                _logger.warning("retry() 被调用但 retry_pending 为 False，跳过")
                return {"interrupted": False, "session_id": None,
                        "delta": {"input": 0, "output": 0, "calls": 0},
                        "pending": False}
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

        与 run_round 共享 _execute_round 执行逻辑，
        但省略了 pending_messages 排队和 checkpoint 保存等交互式功能。
        """
        async with self._state.round_lock:
            self._ensure_idle()
            # ★ P1-2 修复: 开始轮次前先转换状态机 IDLE → RUNNING，
            #   与 run_round 的行为一致，确保 StateMachineMiddleware 能正确完成
            #   RUNNING → COMPLETED 的自动转换。
            self._state_machine.start_round()
            try:
                self._agent.add_user_message(prompt)
            except (KeyboardInterrupt, asyncio.CancelledError):
                raise
            except Exception:
                _logger.exception("run_single: add_user_message 异常")
                self._force_state_recovery()
                raise
            # ── AI 回复前提前分配 session_id ──────────────────
            self._session_id_newly_allocated = not self._state.session_id
            if not self._state.session_id:
                self._state.session_id = self._persistence_port.generate_id()
            try:
                async with self._handle_round_error("run_single"):
                    result = await self._execute_round()
            except (KeyboardInterrupt, asyncio.CancelledError):
                raise
            except Exception:
                # ★ P2 修复：_execute_round 异常后的消息回滚，
                #   与 run_round 的 _rollback_round_on_error 等效，
                #   清理已添加的 user 消息和 session_id，保持消息列表一致性。
                if self._agent.messages and self._agent.messages[-1].get(_ROLE_KEY) == "user":
                    self._agent.messages.pop()
                    _logger.warning("run_single 异常，已回滚最后一条 user 消息")
                # 回滚 orphan ID（仅限新分配的 session_id，已从持久化加载的保留）
                if getattr(self, '_session_id_newly_allocated', False):
                    self._state.session_id = None
                if self._ctx_mgr is not None:
                    self._ctx_mgr.invalidate_cache()
                raise
            return result

    # ── _execute_round 子方法（委托给 _RoundEngine） ───────

    def _prepare_round(self) -> None:
        """更新 agent 和 context_manager 的模型配置。委托给 _RoundEngine。"""
        self._round_engine._prepare_round()

    def _snapshot_token_stats(self) -> tuple[int, int, int]:
        """获取前置 token 统计快照。委托给 _RoundEngine。"""
        return self._round_engine._snapshot_token_stats()

    async def _finalize_round(self, interrupted: bool,
                              prev_stats: tuple[int, int, int],
                              checkpoint_requested: bool = False) -> dict:
        """后置处理。委托给 _RoundEngine。"""
        return await self._round_engine._finalize_round(interrupted, prev_stats, checkpoint_requested)

    async def _execute_round(self) -> dict:
        """执行一轮对话的公共逻辑（编排方法）。委托给 _RoundEngine。"""
        return await self._round_engine.execute_round()

    def _compute_token_delta(self, prev_input: int, prev_output: int, prev_calls: int) -> tuple[dict, dict]:
        """计算本轮 token 消耗增量。委托给 _RoundEngine。"""
        return self._round_engine._compute_token_delta(prev_input, prev_output, prev_calls)

    async def _auto_save(self) -> str | None:
        """自动保存会话。委托给 _RoundEngine。"""
        return await self._round_engine._auto_save()

    async def _emit_round_events(self, interrupted: bool, session_id: str | None,
                                  delta: dict, current: dict,
                                  checkpoint_requested: bool = False) -> dict:
        """发射 round 事件。委托给 _RoundEngine。"""
        return await self._round_engine._emit_round_events(
            interrupted, session_id, delta, current, checkpoint_requested)

    # ── 消息管理 ──────────────────────────────────────────

    def add_user_message(self, content: str) -> None:
        """追加用户消息。"""
        return add_message(self, content)

    def clear_messages(self) -> int:
        """清空对话（保留 system prompt）。

        必须在非 RUNNING 状态下调用（RUNNING 状态下不允许 clear）。
        如需在 RUNNING 状态下清空，先 interrupt() 再 clear()。

        Returns:
            被删除的消息数量
        """
        # ★ P0 修复: RUNNING→clear 转换已从 state_machine 移除。
        #   clear_messages() 在 RUNNING 状态下被调用时，状态机转换会失败，
        #   但消息仍被清空（仅限非 RUNNING 调用场景）。
        try:
            self._state_machine.clear()
        except InvalidTransitionError:
            _logger.warning(
                "clear_messages 被跳过：状态机当前为 %s，不允许 clear 转换",
                self._state_machine.name,
            )
            return 0

        system_msgs = self._system_messages
        removed = len(self._agent.messages) - len(system_msgs)
        self._agent.messages[:] = system_msgs
        self.sync_retry_pending()
        # 清空 sandbox
        sm = get_sandbox_manager()
        if sm:
            sm.clear()
        self._emit("messages_changed", action="clear", removed=removed)

        # 更新仪表盘
        self._metrics.gauge("session.messages", 0)
        # ★ P2-2 修复：清空 captured_prefill，确保重新开始时不残留之前捕获的文本
        self.captured_prefill = ''
        return removed

    def undo_last_round(self) -> int:
        """撤销上一轮对话（移除末尾的 assistant + tool + user 消息）。

        Returns:
            移除的消息数量
        """
        removed = 0
        while self._agent.messages and self._agent.messages[-1][_ROLE_KEY] in ("assistant", "tool"):
            self._agent.messages.pop()
            removed += 1
        if self._agent.messages and self._agent.messages[-1][_ROLE_KEY] == "user":
            self._agent.messages.pop()
            removed += 1
        self._emit("messages_changed", action="undo", removed=removed)
        self._metrics.gauge("session.messages", len(self._agent.messages))
        # ★ Bug E 修复：undo 后同步 retry_pending
        self.sync_retry_pending()
        return removed

    def add_system_message(self, content: str) -> None:
        """追加系统消息。"""
        self._agent.messages.append({_ROLE_KEY: _SYSTEM_ROLE, "content": content})
        self._emit("messages_changed", action="add_system")

    def compress(self, force: bool = True) -> None:
        """手动执行上下文压缩（同步版本，会阻塞事件循环）。

        注意：此方法同步调用 LLM 生成摘要，会在 asyncio 上下文中
        阻塞事件循环。建议优先使用 async_compress()。

        Args:
            force: 是否强制全量压缩
        """
        if self._ctx_mgr is None:
            _logger.warning("ContextManager 未初始化，无法压缩")
            return
        # ★ Bug J 修复：排除 [对话摘要] 标记的 system 消息
        non_system_count = sum(
            1 for m in self._agent.messages
            if m.get(_ROLE_KEY) != _SYSTEM_ROLE
            and not (m.get("content") or "").startswith("[对话摘要]")
        )
        if non_system_count <= _MIN_NON_SYSTEM_FOR_COMPRESS:
            _logger.info("非系统消息太少（≤%d），无需压缩", _MIN_NON_SYSTEM_FOR_COMPRESS)
            return
        self._ctx_mgr.check_and_compress(force=force)

    async def async_compress(self, force: bool = True) -> None:
        """异步执行上下文压缩（推荐使用）。

        将同步的 check_and_compress 移到线程池执行，
        避免在 asyncio 上下文中阻塞事件循环 5-30 秒。

        Args:
            force: 是否强制全量压缩
        """
        if self._ctx_mgr is None:
            _logger.warning("ContextManager 未初始化，无法压缩")
            return
        # ★ Bug J 修复：排除 [对话摘要] 标记的 system 消息
        non_system_count = sum(
            1 for m in self._agent.messages
            if m.get(_ROLE_KEY) != _SYSTEM_ROLE
            and not (m.get("content") or "").startswith("[对话摘要]")
        )
        if non_system_count <= _MIN_NON_SYSTEM_FOR_COMPRESS:
            _logger.info("非系统消息太少（≤%d），无需压缩", _MIN_NON_SYSTEM_FOR_COMPRESS)
            return
        await asyncio.to_thread(self._ctx_mgr.check_and_compress, force=force)

    # ── 会话持久化 ────────────────────────────────────────

    def save(self) -> str | None:
        """保存当前会话到 .chat/msg_list/。

        状态转换: COMPLETED/INTERRUPTED/IDLE → IDLE

        Returns:
            session_id，无可保存内容时返回 None
        """
        return save_session(self)

    def load(self, session_id: str) -> dict | None:
        """加载历史会话。

        会替换当前非 system 消息，保留当前 system prompt。
        根据最后一条消息的角色设置状态机的 retry 能力。

        Args:
            session_id: 会话 ID（可带或不带 .json 后缀）

        Returns:
            会话数据字典，不存在时返回 None
        """
        return load_session_data(self, session_id)

    def list_sessions(self) -> list[dict]:
        """列出所有保存的会话。"""
        return list_sessions_fn(self)

    def get_session_ids(self) -> list[str]:
        """列出所有保存的会话 ID。"""
        return get_session_ids_fn(self)

    # ── 断点管理 ──────────────────────────────────────────

    async def save_checkpoint(self) -> None:
        """保存断点（任务中断时调用）- 异步版本。"""
        from ._session_persistence import save_checkpoint_session as _save_cp
        await _save_cp(self)

    async def clear_checkpoint(self) -> None:
        """清除断点（任务成功完成时调用）- 异步版本。"""
        from ._session_persistence import clear_checkpoint_session as _clear_cp
        await _clear_cp(self)

    async def load_checkpoint(self) -> dict | None:
        """加载断点数据 - 异步版本。"""
        from ._session_persistence import load_checkpoint_data as _load_cp
        return await _load_cp(self)

    async def has_checkpoint(self) -> bool:
        """检查是否存在有效断点 - 异步版本。"""
        return await has_checkpoint_session(self)

    def resume_from_checkpoint(self) -> bool:
        """从断点恢复任务。

        Returns:
            是否成功恢复
        """
        return resume_from_checkpoint_session(self)

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
