"""消费者 API — ChatUIConsumer 兼容实现（薄外观）。

ChatUIConsumer 作为薄外观（Thin Facade），将职责委托给三个独立组件：
  - TuiAssembly（_assembly.py）— 子系统装配
  - TuiLifecycle（_lifecycle.py）— 生命周期管理
  - TuiInputOrchestrator（_input_orchestrator.py）— 输入等待编排

设计模式: 外观（Facade）— ChatUIConsumer 保持所有公开 API 签名不变。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from src.tui._renderer import TuiEngine, TuiRenderer, EventDispatcher
    from src.tui._bottom_bar import _BottomBar
    from src.tui._input import Input
    from src.tui._completion import _CmplHandler
    from src.tui.state.render_state import ChatRenderState
    from src.tui.events.event_types import (
        ContentChunkEvent,
        ModelPhaseEvent,
        OutputEvent,
        ParseInfoDoneEvent,
        ParseInfoEvent,
        PhaseDoneEvent,
        ReasoningChunkEvent,
        ToolDoneEvent,
        ToolOutputChunkEvent,
        ToolParsingEvent,
        ToolStartedEvent,
        ToolSummaryEvent,
    )

from src.tui._const import (
    UserMsgCmd, NotificationCmd, ErrorCmd,
    WriteLineCmd, DisplayMsgsCmd,
)
from src.tui._locks import render_lock
from src.tui.state.consumer_registry import (
    _register_consumer,
    _unregister_consumer,
)
from src.tui._assembly import TuiAssembly, TuiAssemblyResult
from src.tui._lifecycle import TuiLifecycle
from src.tui._input_orchestrator import TuiInputOrchestrator

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# 向后兼容的组件命名空间
# ═══════════════════════════════════════════════════════════

class _ComponentsNamespace:
    """向后兼容的组件命名空间。

    Attributes:
        input: 统一输入管理实例。
    """
    __slots__ = ('input',)

    def __init__(self, input_instance: "Input | None" = None):
        self.input = input_instance


# ═══════════════════════════════════════════════════════════
# ChatUIConsumer — 对外公开 API（薄外观）
# ═══════════════════════════════════════════════════════════

class ChatUIConsumer:
    """终端聊天消费者 — TUI 统一入口（薄外观）。

    将职责委托给三个独立组件：
      - TuiAssembly：组装子系统
      - TuiLifecycle：start/stop/suspend/resume
      - TuiInputOrchestrator：wait_for_user_input

    公开方法与旧版完全兼容。
    """

    def __init__(self, event_bus=None):
        """初始化 ChatUIConsumer。

        Args:
            event_bus: DisplayEventBus 实例。为 None 时获取默认实例。
        """
        if event_bus is None:
            from src.tui.events.event_bus import DisplayEventBus
            event_bus = DisplayEventBus.get_default()
        self._bus = event_bus

        # ── 通过 TuiAssembly 装配子系统 ──
        result: TuiAssemblyResult = TuiAssembly.assemble(
            on_display_messages=self._display_messages_handler,
        )
        self._rs = result.rs
        self._engine: "TuiEngine" = result.engine
        self._bb: "_BottomBar" = result.bb
        self._dispatcher: "EventDispatcher" = result.dispatcher
        self._renderer: "TuiRenderer" = result.renderer
        self._cmpl_handler: "_CmplHandler" = result.cmpl_handler
        self._input: "Input" = result.input_instance
        self._subagent_controller = result.subagent_controller
        self._components = result.components

        # ── 委托组件 ──
        self._lifecycle = TuiLifecycle(
            engine=self._engine, bus=self._bus, bb=self._bb,
            rs=self._rs, dispatcher=self._dispatcher,
            subagent_controller=self._subagent_controller,
        )
        self._input_orchestrator = TuiInputOrchestrator(self._input)

        # ── InputReader 暂不启用（process_events 尚未集成队列消费路径） ──

    @classmethod
    def for_testing(cls, components, event_bus=None) -> "ChatUIConsumer":
        """创建用于测试的 ChatUIConsumer 实例。

        支持新旧两种 ``components`` 类型：
          - 旧 ``_ChatUIComponents``：提取内部属性
          - ``dict``：按 key 取值

        Args:
            components: 预创建的组件容器或字典。
            event_bus: DisplayEventBus 实例。

        Returns:
            新的 ChatUIConsumer 实例。
        """
        if event_bus is None:
            from src.tui.events.event_bus import DisplayEventBus
            event_bus = DisplayEventBus.get_default()
        instance = cls.__new__(cls)
        instance._bus = event_bus

        # 兼容旧的 _ChatUIComponents 和新接口
        if hasattr(components, 'rs'):
            instance._rs = components.rs
            instance._engine = components.engine
            instance._bb = components.bottom_bar
            instance._dispatcher = components.dispatcher
            instance._renderer = components.tui_renderer
            instance._cmpl_handler = components.cmpl_handler
            instance._input = components.input
        else:
            instance._rs = components.get('rs')
            instance._engine = components.get('engine')
            instance._bb = components.get('bottom_bar')
            instance._dispatcher = components.get('dispatcher')
            instance._renderer = components.get('tui_renderer')
            instance._cmpl_handler = components.get('cmpl_handler')
            instance._input = components.get('input')

        instance._subagent_controller = None
        instance._components = _ComponentsNamespace(instance._input)
        instance._lifecycle = TuiLifecycle(
            engine=instance._engine, bus=instance._bus, bb=instance._bb,
            rs=instance._rs, dispatcher=instance._dispatcher,
        )
        instance._input_orchestrator = TuiInputOrchestrator(instance._input)
        return instance

    # ── 生命周期（委托 TuiLifecycle） ─────────────

    @property
    def _started(self):
        return self._lifecycle.is_started

    @property
    def _handlers_bound(self):
        return self._lifecycle.handlers_bound

    @property
    def _bound_handlers(self):
        return getattr(self._lifecycle, '_bound_handlers', None)

    @_bound_handlers.setter
    def _bound_handlers(self, value):
        self._lifecycle._bound_handlers = value

    @property
    def _state_lock(self):
        return self._lifecycle._state_lock

    def start(self) -> None:
        """启动 ChatUI 消费者（委托 TuiLifecycle + 注册消费者）。"""
        if not self._started:
            _register_consumer(self)
        self._lifecycle.start()

    def stop(self) -> None:
        """停止 ChatUI 消费者（委托 TuiLifecycle + 注销消费者）。"""
        if self._started:
            self._lifecycle.stop()
            _unregister_consumer()

    def suspend(self) -> None:
        """暂停渲染引擎（委托 TuiLifecycle）。"""
        self._lifecycle.suspend()

    def resume(self) -> None:
        """恢复渲染引擎（委托 TuiLifecycle）。"""
        self._lifecycle.resume()

    # ── 公开方法 ──────────────────────────────────

    def on_user_message(self, text: str) -> None:
        self._engine.push_cmd(UserMsgCmd(text=text))

    def on_notification(self, text: str) -> None:
        self._engine.push_cmd(NotificationCmd(text=text))

    def on_error(self, message: str) -> None:
        if not message:
            return
        self._engine.push_cmd(ErrorCmd(message=message))

    def register_event_handler(self, event_type: type, handler_method: Callable) -> None:
        """注册自定义事件处理器（委托 TuiLifecycle）。"""
        self._lifecycle.register_event_handler(event_type, handler_method)

    def _display_messages_handler(self, messages: list[dict], speed: int) -> None:
        """渲染消息列表到上屏区域。"""
        if not messages:
            return
        from src.core.constants import DIM, RESET
        for msg in messages:
            role = msg.get("role", "")
            content = str(msg.get("content", ""))
            if role == "user":
                line = f"\n  \033[1;38;5;81m>\033[0m \033[38;5;252m{content}\033[0m\n"
            elif role == "assistant":
                line = f"  \033[38;5;242m\u2502\033[0m {content}"
            elif role == "system":
                continue
            else:
                line = f"  \033[38;5;242m\u2502\033[0m {content}"
            self._engine.push_cmd(WriteLineCmd(text=line))
        self._engine.push_cmd(WriteLineCmd(text=f"  {DIM}{'─' * 40}{RESET}"))
        self._engine.flush()

    def request_bottom_redraw(self) -> None:
        self._engine.request_bottom_redraw()

    def write_line(self, text: str) -> None:
        self._engine.push_cmd(WriteLineCmd(text=text))

    def display_messages(self, messages: list[dict], speed: int = 0) -> None:
        self._engine.push_cmd(DisplayMsgsCmd(messages=messages, speed=speed))

    def wait_for_user_input(
        self, monitor, prefill: str = "", timeout: float | None = None,
        input_=None,
    ) -> str:
        """阻塞等待用户输入（委托 TuiInputOrchestrator）。"""
        return self._input_orchestrator.wait_for_user_input(
            monitor, prefill=prefill, timeout=timeout, input_=input_,
        )

    def setup_completion(self, input_) -> None:
        """设置 Tab 补全回调到 Input 实例。"""
        input_.set_completion_callback(self._cmpl_handler.on_tab)
        input_.set_dismiss_completion_callback(self._cmpl_handler.on_dismiss)
        input_.set_completion_navigate_callback(self._cmpl_handler.on_navigate)
        input_.set_auto_completion_callback(self._cmpl_handler.on_auto)

    @property
    def input(self):
        return self._input

    @property
    def bottom_bar(self):
        return self._bb

    @property
    def output_adapter(self):
        return self._renderer.output_adapter

    def set_panel_refresh_callback(self, callback: Callable[[], None] | None) -> None:
        self._engine.set_panel_refresh_callback(callback)

    def setup_bottom_bar(self) -> None:
        with render_lock:
            self._bb.setup()

    def teardown_bottom_bar(self) -> None:
        self._bb.teardown()

    def ensure_cursor_upper(self) -> None:
        self._engine.ensure_cursor_upper()

    def refresh_bottom_bar(self, text: str, cursor_pos: int = -1) -> None:
        effective_pos = len(text) if cursor_pos < 0 else cursor_pos
        self._bb.set_input_state(text, effective_pos)
        self._engine.request_bottom_redraw()

    def flush(self, timeout: float | None = 5.0) -> None:
        self._engine.flush(timeout=timeout)

    def push_cmd(self, cmd: tuple) -> None:
        self._engine.push_cmd(cmd)
