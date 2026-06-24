"""消费者 API — ChatUIConsumer 公开接口。

从 _tui.py 拆分，组件化 TUI 架构的顶层入口，管理所有子系统的生命周期。

P1-2 重构：生命周期方法委托给 ChatUILifecycle，输入方法委托给 ChatUIInputCoordinator。
"""

from __future__ import annotations

import logging
import os
import threading
from typing import TYPE_CHECKING, Any, Callable

from .lifecycle.lifecycle import ChatUILifecycle
from .input.coordinator import ChatUIInputCoordinator

if TYPE_CHECKING:
    from ..ui.events.event_types import (
        ReasoningChunkEvent,
        ContentChunkEvent,
        PhaseDoneEvent,
        ToolDoneEvent,
        ToolOutputChunkEvent,
        ToolStartedEvent,
        ToolSummaryEvent,
        ParseInfoEvent,
        ParseInfoDoneEvent,
        OutputEvent,
        ModelPhaseEvent,
    )

from .commands.types import (
    CmdUserMsg,
    CmdNotification,
    CmdError,
    CmdWriteLine,
    CmdDisplayMsgs,
)

from .state.app_state import (
    _register_consumer,
    _unregister_consumer,
    _active_consumer,
    get_active_chat_ui,
)

from .infrastructure.lock import output_lock

try:
    from .input.prompt_input import PromptInputManager, _PROMPT_TOOLKIT_AVAILABLE
except ImportError:
    _PROMPT_TOOLKIT_AVAILABLE = False
    PromptInputManager = None  # type: ignore[assignment]

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# ChatUIConsumer — 对外公开 API（组件化架构）
# ═══════════════════════════════════════════════════════════

class ChatUIConsumer:
    """终端聊天消费者 — 组件化 TUI 架构（薄门面）。

    内部子系统（由 ChatUILifecycle.create_subsystems 批量创建）：
      _rs       (_RenderState)    — 渲染器生命周期
      _engine   (TuiEngine)       — render 线程 + 命令队列
      _disp     (EventDispatcher) — 事件过滤+入队
      _tui_renderer (TuiRenderer) — 组件化渲染分发
      _cmpl     (_CmplHandler)    — Tab 补全交互

    协调器：
      _lifecycle (ChatUILifecycle)        — start/stop/suspend/resume
      _input_coord (ChatUIInputCoordinator) — 输入等待/补全/底部栏
    """

    def __init__(self, event_bus=None):
        if event_bus is None:
            from ..ui.events.event_bus import DisplayEventBus
            event_bus = DisplayEventBus.get_default()
        self._bus = event_bus

        # ── 子系统批量创建（委托给 ChatUILifecycle 静态工厂） ──
        subsystems = ChatUILifecycle.create_subsystems(self)
        self._rs = subsystems["_rs"]
        self._cursor_tracker = subsystems["_cursor_tracker"]
        self._bottom_bar = subsystems["_bottom_bar"]
        self._tio = subsystems["_tio"]
        self._tui_renderer = subsystems["_tui_renderer"]
        self._engine = subsystems["_engine"]
        self._disp = subsystems["_disp"]
        self._cmpl = subsystems["_cmpl"]
        self._completion_engine = subsystems["_completion_engine"]

        # ── prompt_toolkit 输入管理器（可选依赖） ──
        self._prompt_input: PromptInputManager | None = None
        if (_PROMPT_TOOLKIT_AVAILABLE
                and os.environ.get('CHAT_UI_USE_PROMPT_TOOLKIT', '').lower()
                in ('1', 'true', 'yes', 'on')):
            self._prompt_input = PromptInputManager()
            self._prompt_input.set_completion_engine(self._completion_engine)
            _logger.debug("PromptInputManager 已初始化（prompt_toolkit 可用）")

        # ── 生命周期/输入协调器 ──
        self._lifecycle = ChatUILifecycle(self)
        self._input_coord = ChatUIInputCoordinator(self)

        self._bound_handlers: dict[type, Any] | None = None
        self._state_lock = threading.Lock()

    # ── 生命周期（委托给 ChatUILifecycle） ──────

    @property
    def _started(self) -> bool:
        """_started 属性委托给 ChatUILifecycle.started。"""
        return self._lifecycle.started

    @_started.setter
    def _started(self, value: bool) -> None:
        """设置 _started 状态（仅供测试使用）。"""
        self._lifecycle._started = value

    def start(self) -> None:
        """启动 ChatUI 消费者。

        委托给 ChatUILifecycle.start()。
        """
        ref = [self._bound_handlers]
        self._lifecycle.start(
            state_lock=self._state_lock,
            bound_handlers_ref=ref,
            bus=self._bus,
            disp=self._disp,
            engine=self._engine,
            register_fn=_register_consumer,
        )
        self._bound_handlers = ref[0]

        # 注册 ChatUIPort 到全局默认端口，使 core 层可通过端口接口访问 ChatUI
        from .port_adapter import register_chat_ui_port
        register_chat_ui_port(self)

        # 注册回调到 ui/_lock.py，替代 ui/ → chat_ui 的直接 import
        # 依赖方向：chat_ui → ui（单向），符合架构分层
        from ..ui._lock import register_write_line_callback, register_is_chat_ui_active_callback
        register_write_line_callback(self.write_line)
        register_is_chat_ui_active_callback(lambda: get_active_chat_ui() is not None)

    def stop(self) -> None:
        """停止 ChatUI 消费者。

        委托给 ChatUILifecycle.stop()。
        """
        ref = [self._bound_handlers]
        self._lifecycle.stop(
            state_lock=self._state_lock,
            bound_handlers_ref=ref,
            bus=self._bus,
            engine=self._engine,
            rs=self._rs,
            bottom_bar=self._bottom_bar,
            output_lock=output_lock,
            unregister_fn=_unregister_consumer,
        )
        self._bound_handlers = ref[0]

        # 从全局默认端口注销 ChatUIPort
        from .port_adapter import unregister_chat_ui_port
        unregister_chat_ui_port()

    def suspend(self) -> None:
        """暂停渲染引擎，供交互式工具独占终端。

        委托给 ChatUILifecycle.suspend()。
        """
        self._lifecycle.suspend(
            state_lock=self._state_lock,
            engine=self._engine,
            bottom_bar=self._bottom_bar,
            output_lock=output_lock,
        )

    def resume(self) -> None:
        """恢复渲染引擎，重建底部栏。

        委托给 ChatUILifecycle.resume()。
        """
        self._lifecycle.resume(
            state_lock=self._state_lock,
            engine=self._engine,
            bottom_bar=self._bottom_bar,
            tio=self._tio,
            output_lock=output_lock,
        )

    # ── 公开方法 ──────────────────────────────────

    def on_user_message(self, text: str) -> None:
        self._engine.push_cmd(CmdUserMsg(text=text))

    def on_notification(self, text: str) -> None:
        self._engine.push_cmd(CmdNotification(text=text))

    def on_error(self, message: str) -> None:
        if not message:
            return
        self._engine.push_cmd(CmdError(message=message))

    def refresh(self) -> None:
        pass

    def request_bottom_redraw(self) -> None:
        self._engine.request_bottom_redraw()

    def write_line(self, text: str) -> None:
        self._engine.push_cmd(CmdWriteLine(text=text))

    def display_messages(self, messages: list[dict], speed: int = 0) -> None:
        self._engine.push_cmd(CmdDisplayMsgs(messages=messages, speed=speed))

    def wait_for_user_input(self, monitor, prefill: str = "", timeout: float | None = None) -> str:
        """阻塞等待用户通过 monitor 输入文本。

        委托给 ChatUIInputCoordinator.wait_for_user_input()。
        """
        return self._input_coord.wait_for_user_input(monitor, prefill=prefill, timeout=timeout)

    def setup_completion(self, monitor) -> None:
        """为监视器配置补全回调。

        委托给 ChatUIInputCoordinator.setup_completion()。
        """
        self._input_coord.setup_completion(monitor)

    def get_input_manager(self) -> "PromptInputManager | None":
        """返回 PromptInputManager 实例（可选依赖）。

        当 prompt_toolkit 不可用时返回 None，调用方应回退到 EscapeMonitor。
        """
        return self._prompt_input

    @property
    def bottom_bar(self):
        return self._bottom_bar

    @property
    def output_adapter(self):
        return self._tui_renderer.output_adapter

    def set_panel_refresh_callback(self, callback: Callable[[], None] | None) -> None:
        self._engine.set_panel_refresh_callback(callback)

    def setup_bottom_bar(self) -> None:
        """设置底部栏（初始状态）。

        委托给 ChatUIInputCoordinator.setup_bottom_bar()。
        """
        self._input_coord.setup_bottom_bar(output_lock)

    def teardown_bottom_bar(self) -> None:
        self._bottom_bar.teardown()

    def ensure_cursor_upper(self) -> None:
        self._engine.ensure_cursor_upper()

    def get_echo_callback(self):
        """返回 echo 回调函数。

        VNode 路径（CHAT_UI_RENDER_USE_VNODE=1）时返回 push_cmd 版本，
        默认返回原有的 refresh_bottom_bar 版本。
        """
        if getattr(self._engine, '_use_vnode', False):
            from .commands.types import CmdInputChanged

            def _vnode_echo(text: str, cursor_pos: int) -> None:
                self._engine.push_cmd(CmdInputChanged(text=text, cursor_pos=cursor_pos))

            return _vnode_echo
        return self.refresh_bottom_bar

    def refresh_bottom_bar(self, text: str, cursor_pos: int = -1) -> None:
        effective_pos = len(text) if cursor_pos < 0 else cursor_pos
        self._bottom_bar.set_input_state(text, effective_pos)
        self._engine.request_bottom_redraw()

    def flush(self, timeout: float | None = 5.0) -> None:
        self._engine.flush(timeout=timeout)

    def push_cmd(self, cmd: object) -> None:
        self._engine.push_cmd(cmd)


