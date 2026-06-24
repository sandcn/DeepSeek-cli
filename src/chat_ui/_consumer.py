"""消费者 API — ChatUIConsumer 公开接口。

从 _tui.py 拆分，组件化 TUI 架构的顶层入口，管理所有子系统的生命周期。
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from typing import TYPE_CHECKING, Any, Callable

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

from ._const import (
    _ANSI_CURSOR_BOTTOM,
)

from ._cmd import (
    CmdUserMsg,
    CmdNotification,
    CmdError,
    CmdWriteLine,
    CmdDisplayMsgs,
)

from ._state import (
    _register_consumer,
    _unregister_consumer,
    _active_consumer,
    get_active_chat_ui,
)

from ._lock import output_lock

from ._terminal_io import TerminalIO

from ..ui._blessed import get_terminal

from ._engine import TuiEngine
from ._renderer import TuiRenderer, _RenderState
from ._dispatcher import EventDispatcher, _HANDLER_MAP
from ._protocols import BottomBarProtocol
from ._completion import _CmplHandler, _apply_completion

try:
    from ._prompt_input import PromptInputManager, _PROMPT_TOOLKIT_AVAILABLE
except ImportError:
    _PROMPT_TOOLKIT_AVAILABLE = False
    PromptInputManager = None  # type: ignore[assignment]

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# ChatUIConsumer — 对外公开 API（组件化架构）
# ═══════════════════════════════════════════════════════════

class ChatUIConsumer:
    """终端聊天消费者 — 组件化 TUI 架构。

    内部子系统：
      _rs       (_RenderState)    — 渲染器生命周期
      _engine   (TuiEngine)       — render 线程 + 命令队列
      _disp     (EventDispatcher) — 事件过滤+入队
      _renderer (TuiRenderer)     — 组件化渲染分发
      _cmpl     (_CmplHandler)    — Tab 补全交互
    """

    def __init__(self, event_bus=None):
        if event_bus is None:
            from ..ui.events.event_bus import DisplayEventBus
            event_bus = DisplayEventBus.get_default()
        self._bus = event_bus

        from ..ui._cursor_tracker import CursorTracker
        from ..ui._bottom_bar import _BottomBar
        from ..ui._completion import CompletionEngine
        from rich.console import Console  # 仅用于 OutputAdapter 初始化（api 层依赖，不在本次重构范围）
        from ..api.renderer.output import OutputAdapter
        from ..terminal import get_safe_console_config

        self._rs = _RenderState()
        self._cursor_tracker = CursorTracker()
        self._bottom_bar = _BottomBar(cursor_tracker=self._cursor_tracker)
        self._tio = TerminalIO(lock=output_lock)

        console = Console(**get_safe_console_config(), file=sys.__stdout__)
        output_adapter = OutputAdapter(console)

        from ..ui.tui._message_display import _display_messages

        self._tui_renderer = TuiRenderer(
            self._rs, output_adapter, self._bottom_bar,
            on_display_messages=_display_messages,
            cursor_tracker=self._cursor_tracker,
            terminal_io=self._tio,
        )
        self._engine = TuiEngine(
            self._tui_renderer, self._bottom_bar,
            cursor_tracker=self._cursor_tracker,
            terminal_io=self._tio,
        )
        self._disp = EventDispatcher(push_cmd=self._engine.push_cmd)
        self._rs.set_output_adapter(output_adapter)
        self._completion_engine = CompletionEngine()
        self._cmpl = _CmplHandler(
            self._bottom_bar, self._completion_engine,
            request_redraw=self._engine.request_bottom_redraw,
        )
        # ── prompt_toolkit 输入管理器（可选依赖） ──
        self._prompt_input: PromptInputManager | None = None
        if (_PROMPT_TOOLKIT_AVAILABLE
                and os.environ.get('CHAT_UI_USE_PROMPT_TOOLKIT', '').lower()
                in ('1', 'true', 'yes', 'on')):
            self._prompt_input = PromptInputManager()
            self._prompt_input.set_completion_engine(self._completion_engine)
            _logger.debug("PromptInputManager 已初始化（prompt_toolkit 可用）")
        self._bound_handlers: dict[type, Any] | None = None
        self._state_lock = threading.Lock()
        self._started = False

    # ── 生命周期 ──────────────────────────────────

    def start(self) -> None:
        """启动 ChatUI 消费者。

        订阅 11 种 DisplayEvent、先取消已有绑定避免重复注册、
        启动渲染线程、注册为活跃消费者。幂等操作——重复调用安全返回。

        Thread safety: _started 读写由 _state_lock 保护。
        """
        with self._state_lock:
            if self._started:
                return
            if self._bound_handlers is None:
                self._bound_handlers = {}
                from ..ui.events.event_types import (
                    ReasoningChunkEvent, ContentChunkEvent, PhaseDoneEvent,
                    ToolStartedEvent, ToolDoneEvent, ToolOutputChunkEvent,
                    ToolSummaryEvent, ParseInfoEvent, ParseInfoDoneEvent,
                    OutputEvent, ModelPhaseEvent,
                )
                _event_type_map = {
                    "ReasoningChunkEvent": ReasoningChunkEvent,
                    "ContentChunkEvent": ContentChunkEvent,
                    "PhaseDoneEvent": PhaseDoneEvent,
                    "ToolStartedEvent": ToolStartedEvent,
                    "ToolDoneEvent": ToolDoneEvent,
                    "ToolOutputChunkEvent": ToolOutputChunkEvent,
                    "ParseInfoEvent": ParseInfoEvent,
                    "ParseInfoDoneEvent": ParseInfoDoneEvent,
                    "OutputEvent": OutputEvent,
                    "ModelPhaseEvent": ModelPhaseEvent,
                    "ToolSummaryEvent": ToolSummaryEvent,
                }
                for key, (_, handler_name) in _HANDLER_MAP.items():
                    event_type = _event_type_map[key]
                    handler = getattr(self._disp, handler_name)
                    self._bound_handlers[event_type] = handler
            for event_type in self._bound_handlers:
                try:
                    self._bus.unsubscribe(self._bound_handlers[event_type], event_type=event_type)
                except Exception:
                    _logger.debug("start: unsubscribe %s 失败", event_type.__name__, exc_info=True)
            for event_type in self._bound_handlers:
                self._bus.subscribe(self._bound_handlers[event_type], event_type=event_type)
            _register_consumer(self)
            self._engine.start()
            self._started = True

    def stop(self) -> None:
        """停止 ChatUI 消费者。

        取消所有事件订阅、排空命令队列、停止渲染引擎、
        注销活跃消费者、清理渲染状态和底部栏。

        Thread safety: _started 读写由 _state_lock 保护。
        """
        with self._state_lock:
            if not self._started:
                return
            if self._bound_handlers is not None:
                for event_type in self._bound_handlers:
                    try:
                        self._bus.unsubscribe(self._bound_handlers[event_type], event_type=event_type)
                    except Exception:
                        _logger.debug("stop: unsubscribe %s 失败", event_type.__name__, exc_info=True)
            self._engine.flush()
            self._engine.stop()
            _unregister_consumer()
            with output_lock:
                self._rs.close_all()
                self._bottom_bar.teardown()
            self._started = False

    def suspend(self) -> None:
        """暂停渲染引擎，供交互式工具独占终端。

        停止 render 线程并拆除底部栏，释放终端控制权。
        必须已启动（_started = True）才有效。

        Thread safety: _started 检查由 _state_lock 保护。
        """
        with self._state_lock:
            if not self._started:
                return
            self._engine.flush()
            self._engine.stop()
            with output_lock:
                self._bottom_bar.teardown()

    def resume(self) -> None:
        """恢复渲染引擎，重建底部栏。

        重新获取终端尺寸、重绘底部栏并启动 render 线程。
        必须已启动（_started = True）且引擎未运行。

        Thread safety: _started 检查由 _state_lock 保护。
        """
        with self._state_lock:
            if not self._started:
                return
            if self._engine._render_running:
                return
            with output_lock:
                try:
                    term = get_terminal()
                    self._tio.write(term.move_xy(0, term.height - 1))
                except Exception:
                    _logger.debug("resume 光标定位失败, 使用 ANSI 回退", exc_info=True)
                    self._tio.write(_ANSI_CURSOR_BOTTOM)
                self._tio.flush()
                self._bottom_bar.setup()
                self._engine.start()

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

        轮询 monitor.get_queued_input()，以 50ms 间隔检查。

        Args:
            monitor: 输入监视器，需提供 get_queued_input() / set_prefill()
            prefill: 预填充文本（可选）
            timeout: 超时秒数，None 表示无限等待

        Returns:
            用户输入文本；超时时返回空字符串 ""
        """
        if prefill:
            monitor.set_prefill(prefill)
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            text = monitor.get_queued_input()
            if text is not None:
                return text
            if deadline is not None and time.monotonic() >= deadline:
                return ""
            time.sleep(0.05)

    def setup_completion(self, monitor) -> None:
        monitor.set_completion_callback(self._cmpl.on_tab)
        monitor.set_dismiss_completion_callback(self._cmpl.on_dismiss)
        monitor.set_completion_navigate_callback(self._cmpl.on_navigate)
        monitor.set_auto_completion_callback(self._cmpl.on_auto)

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
        with output_lock:
            self._bottom_bar.setup()

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
            from ._cmd import CmdInputChanged

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


