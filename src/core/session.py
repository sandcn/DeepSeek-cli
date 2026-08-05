"""ChatSession — 纯领域会话对象（薄委托层）

职责范围：
1. 对话生命周期管理 → 委托给 _session_lifecycle 模块（run_round/retry/run_single/run_pending_loop）
2. 消息管理 → 委托给 SessionMessagingManager
3. 会话持久化 → 委托给 SessionPersistenceManager
4. Agent + ContextManager 编排
5. 通过 Hook 机制解耦 UI 层
6. 通过 Telemetry 提供可观测性数据

已提取的子模块：
- _session_lifecycle.py — 生命周期编排（run_round/retry/run_single/run_pending_loop）
- _session_persistence_manager.py — 会话持久化（save/load/auto-save/checkpoint）
- _session_messaging_manager.py — 消息管理（add/clear/undo/compress）
- _session_messages.py — 消息操作函数（add_message/non_system_messages/system_messages）
- _session_state.py — 会话可变状态容器
- _session_compression.py — 压缩前置条件验证

设计原则：
- 零依赖 UI 层（无 ui. 包导入）
- 可测试：所有依赖可注入
- 可扩展：Hook 系统支持任意前端适配
- 状态形式化：SessionStateMachine 消除布尔状态组合
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable

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
from ..core.adapters.config import DefaultConfigAdapter
from ..core.adapters.observability import DefaultObservabilityAdapter
from ..core.adapters.null import _NullDisplayPort, _NullEventPort, _NullOutputPort  # noqa: F401 — re-exported
from .internal.session._session_messages import add_message, non_system_messages, system_messages
from .internal.session._session_state import SessionState as _SessionData
from .internal.session._session_compression import _validate_compress_preconditions
from .internal.session._session_persistence_manager import SessionPersistenceManager
from .internal.session._session_messaging_manager import SessionMessagingManager
from .internal.session import _session_lifecycle

_logger = logging.getLogger(__name__)

# ── 魔法数字常量 ─────────────────────────────────────
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
        session.on("round_end", lambda **kw: publish_output(str(kw), level="info"))
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
        if persistence_port is not None:
            self._persistence_port = persistence_port
        else:
            from ..core.adapters.persistence import JsonFilePersistence
            self._persistence_port = JsonFilePersistence()
        if checkpoint_port is not None:
            self._checkpoint_port = checkpoint_port
        else:
            from ..core.adapters.persistence import JsonFileCheckpoint
            self._checkpoint_port = JsonFileCheckpoint()
        self._config_port = config_port or DefaultConfigAdapter()

        # ── 核心对象 ──────────────────────────────────────
        self._model: str = model or self._config_port.get_model()

        # 创建 Agent（注入 NullPort 避免 UI 依赖，传递 observability_port）
        null_display = _NullDisplayPort()
        null_event = _NullEventPort()
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
            # SubAgent 完整聊天记录：保存时收集、加载时恢复到 Agent（供 /export 导出）
            subagents_getter=lambda: list(getattr(self._agent, "_subagent_records", None) or []),
            subagents_setter=lambda v: setattr(self._agent, "_subagent_records", list(v or [])),
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

    def reset_retry_pending(self) -> None:
        """强制重置 retry_pending 为 False。

        在某些场景下，调用方需要绕过 sync_retry_pending() 的自动推导逻辑，
        主动清除 retry_pending 标志。例如 /editmsg 编辑后，用户期望预填
        旧内容到编辑行重发，而非自动续接回复。
        """
        self._state.retry_pending = False

    def reset_retry_pending_for_edit(self, has_prefill: bool) -> None:
        """编辑命令统一的 retry_pending 重置入口（模板方法）。

        编辑/截断消息后强制重置 retry_pending = False。编辑语义 =
        用户要重新编辑输入，不是自动续接。无论 prefill 是否为空，
        均无条件重置。has_prefill 参数保留给未来扩展（如日志/可观测性）。

        设计模式: 模板方法（Template Method）— 编辑类命令的统一
        retry_pending 重置骨架，deitmsg/editmsg 等插件共用此入口。
        """
        self._state.retry_pending = False

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
            config_port=self._config_port,
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

    # ── 核心对话方法（委托给 _session_lifecycle 模块） ──

    async def run_round(self, user_input: str) -> dict:
        """添加用户消息并执行一轮对话。委托给 _session_lifecycle.run_round。"""
        return await _session_lifecycle.run_round(self, user_input)

    async def retry(self) -> dict:
        """重新执行上一轮对话。委托给 _session_lifecycle.retry。"""
        return await _session_lifecycle.retry(self)

    async def run_single(self, prompt: str) -> dict:
        """单次对话模式。委托给 _session_lifecycle.run_single。"""
        return await _session_lifecycle.run_single(self, prompt)

    async def run_pending_loop(self, max_iter: int = _MAX_PENDING_LOOP_ITER) -> tuple:
        """处理排队消息。委托给 _session_lifecycle.run_pending_loop。"""
        return await _session_lifecycle.run_pending_loop(self, max_iter)

    def _force_state_recovery(self) -> None:
        """异常后强制恢复状态机。委托给 _session_lifecycle._force_state_recovery。"""
        return _session_lifecycle._force_state_recovery(self)

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
