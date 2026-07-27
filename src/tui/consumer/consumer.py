"""消费者 API — ChatUIConsumer 公开接口。

从 _tui.py 拆分，组件化 TUI 架构的顶层入口，管理所有子系统的生命周期。
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from typing import Protocol

    from ..events.event_types import (
        ReasoningChunkEvent,
        ContentChunkEvent,
        PhaseDoneEvent,
        ToolDoneEvent,
        ToolParsingEvent,
        ToolOutputChunkEvent,
        ToolStartedEvent,
        ToolSummaryEvent,
        ParseInfoEvent,
        ParseInfoDoneEvent,
        OutputEvent,
        ModelPhaseEvent,
    )
    from .factory import _ChatUIComponents

    class _MonitorProtocol(Protocol):
        """EscapeMonitor 的最小接口协议。

        用于 wait_for_user_input() 和 setup_completion() 的类型注解，
        避免运行时循环导入。
        """
        is_alive: bool

        def get_queued_input(self) -> str | None: ...
        def set_prefill(self, text: str) -> None: ...
        def set_completion_callback(self, callback: Callable[[str], str | None]) -> None: ...
        def set_dismiss_completion_callback(self, callback: Callable[[], None]) -> None: ...
        def set_completion_navigate_callback(self, callback: Callable[[int, str], str | None]) -> None: ...
        def set_auto_completion_callback(self, callback: Callable[[str], None]) -> None: ...

from ..engine.const import (
    RenderCommand,
    _ANSI_CURSOR_BOTTOM,
)

from ..state.consumer_registry import (
    _register_consumer,
    _unregister_consumer,
    _active_consumer,
    get_active_chat_ui,
)

from ..engine.lock import render_lock
from ..terminal.blessed import get_terminal

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# ChatUIConsumer — 对外公开 API（组件化架构）
# ═══════════════════════════════════════════════════════════

class ChatUIConsumer:
    """终端聊天消费者 — 组件化 TUI 架构。

    React Ink-like 组件层次：
      MessageStream ─── 滚动消息区（AnswerBlock / ThinkingBlock / ...）
      状态行        ─── 由 _BottomBar._format_status() 渲染
      输入行        ─── 由 _BottomBar._draw_input_lines_locked() 渲染
      Overlay       ─── 由 _CompletionPopup / _BottomBar 渲染

    内部子系统通过 self._components 容器访问：
      _components.rs             (ChatRenderState) — 渲染器生命周期
      _components.engine         (RenderEngine)    — render 线程 + 命令队列
      _components.dispatcher     (EventDispatcher) — 事件过滤+入队
      _components.tui_renderer   (TuiRenderer)     — 组件化渲染分发
      _components.cmpl_handler   (_CmplHandler)    — Tab 补全交互
      _components.bottom_bar     (_BottomBar)      — 底部固定输入栏
      _components.cursor_tracker (CursorTracker)   — 全局光标追踪
    """

    def __init__(self, event_bus=None):
        """初始化 ChatUIConsumer。

        Args:
            event_bus: DisplayEventBus 实例。为 None 时获取默认实例。
        """
        if event_bus is None:
            from ..events.event_bus import DisplayEventBus
            event_bus = DisplayEventBus.get_default()
        self._bus = event_bus

        from .factory import _create_chat_ui_components
        self._components = _create_chat_ui_components(event_bus)

        self._bound_handlers: dict[type, Callable] | None = None
        self._state_lock = threading.Lock()
        self._started = False
        self._handlers_bound = False

    @classmethod
    def for_testing(cls, components: _ChatUIComponents, event_bus=None) -> ChatUIConsumer:
        """创建用于测试的 ChatUIConsumer 实例，注入预创建的组件。

        Args:
            components: 预创建的 _ChatUIComponents 实例。
            event_bus: DisplayEventBus 实例。为 None 时获取默认实例。

        Returns:
            新的 ChatUIConsumer 实例（不调用 __init__）。
        """
        if event_bus is None:
            from ..events.event_bus import DisplayEventBus
            event_bus = DisplayEventBus.get_default()
        instance = cls.__new__(cls)
        instance._bus = event_bus
        instance._components = components
        instance._bound_handlers = None
        instance._state_lock = threading.Lock()
        instance._started = False
        instance._handlers_bound = False
        return instance

    # ── 生命周期 ──────────────────────────────────

    def start(self) -> None:
        """启动 ChatUI 消费者。

        订阅 12 种 DisplayEvent。首次启动时跳过防御性 unsubscribe
        （从未订阅过任何事件）；后续重新启动时先 subscribe 新 handler
        再 unsubscribe 旧 handler，消除事件丢失的时序窗口。
        启动渲染线程、展示品牌屏、注册为活跃消费者。幂等操作——重复调用安全返回。

        Thread safety: _started 读写由 _state_lock 保护。
        """
        with self._state_lock:
            if self._started:
                return
            if self._bound_handlers is None:
                self._bound_handlers = {}
                for event_type, handler in self._components.dispatcher.list_handlers().items():
                    self._bound_handlers[event_type] = handler
            if self._handlers_bound:
                for event_type in self._bound_handlers:
                    try:
                        self._bus.unsubscribe(self._bound_handlers[event_type], event_type=event_type)
                    except Exception:
                        _logger.debug("start: unsubscribe %s 失败", event_type.__name__, exc_info=True)
            for event_type in self._bound_handlers:
                self._bus.subscribe(self._bound_handlers[event_type], event_type=event_type)
            self._handlers_bound = True
            _register_consumer(self)
            self._components.engine.start()
            # ── 展示启动品牌屏 ──
            self._components.engine.push_cmd((RenderCommand.SPLASH,))
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
            self._components.engine.flush()
            self._components.engine.stop()
            _unregister_consumer()
            with render_lock:
                self._components.rs.close_all()
                self._components.bottom_bar.teardown()
            self._started = False
            self._bound_handlers = None

    def suspend(self) -> None:
        """暂停渲染引擎，供交互式工具独占终端。

        停止 render 线程并拆除底部栏，释放终端控制权。
        必须已启动（_started = True）才有效。

        Thread safety: _started 检查由 _state_lock 保护。
        """
        with self._state_lock:
            if not self._started:
                return
            self._components.engine.flush()
            self._components.engine.stop()
            with render_lock:
                self._components.bottom_bar.teardown()

    def resume(self) -> None:
        """恢复渲染引擎，重建底部栏。

        重新获取终端尺寸、重绘底部栏并启动 render 线程。
        必须已启动（_started = True）且引擎未运行。

        Thread safety: _started 检查由 _state_lock 保护。
        """
        with self._state_lock:
            if not self._started:
                return
            if self._components.engine._render_running:
                return
            with render_lock:
                try:
                    term = get_terminal()
                    sys.__stdout__.write(term.move_xy(0, term.height - 1))
                except Exception:
                    _logger.debug("resume 光标定位失败, 使用 ANSI 回退", exc_info=True)
                    sys.__stdout__.write(_ANSI_CURSOR_BOTTOM)
                sys.__stdout__.flush()
                self._components.bottom_bar._active = False
                self._components.bottom_bar.setup()
                self._components.engine.start()

    # ── 公开方法 ──────────────────────────────────

    def on_user_message(self, text: str) -> None:
        self._components.engine.push_cmd((RenderCommand.USER_MSG, text))

    def on_notification(self, text: str) -> None:
        self._components.engine.push_cmd((RenderCommand.NOTIFICATION, text))

    def on_error(self, message: str) -> None:
        if not message:
            return
        self._components.engine.push_cmd((RenderCommand.ERROR, message))

    def register_event_handler(self, event_type: type, handler_method: Callable) -> None:
        """注册自定义事件处理器（委托给 EventDispatcher）。

        允许外部代码在 ChatUIConsumer 启动前/后动态注册额外的事件映射。
        注册的处理器会在 start() 订阅事件总线时自动包含。

        Args:
            event_type: DisplayEvent 子类
            handler_method: 事件处理 callable，签名为 (event) -> None
        """
        self._components.dispatcher.register_handler(event_type, handler_method)
        # 如果已经启动且已绑定，立即订阅新处理器
        with self._state_lock:
            if self._started and self._handlers_bound:
                self._bus.subscribe(handler_method, event_type=event_type)
                if self._bound_handlers is not None:
                    self._bound_handlers[event_type] = handler_method

    def request_bottom_redraw(self) -> None:
        self._components.engine.request_bottom_redraw()

    def write_line(self, text: str) -> None:
        self._components.engine.push_cmd((RenderCommand.WRITE_LINE, text))

    def display_messages(self, messages: list[dict], speed: int = 0) -> None:
        self._components.engine.push_cmd((RenderCommand.DISPLAY_MSGS, messages, speed))

    def wait_for_user_input(self, monitor: _MonitorProtocol, prefill: str = "", timeout: float | None = None) -> str:
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
            if hasattr(monitor, 'is_alive') and not monitor.is_alive:
                raise RuntimeError("EscapeMonitor thread died")
            _logger.debug("wait_for_user_input: about to set_prefill, len=%d, prefill[:50]='%s'", len(prefill), prefill[:50])
            monitor.set_prefill(prefill)
            _logger.debug("wait_for_user_input: set_prefill done, entering poll loop")
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            # monitor 存活检测：EscapeMonitor 线程死后抛出 RuntimeError，
            # 由 _handle_round 捕获并触发恢复逻辑（步骤 4）
            if hasattr(monitor, 'is_alive') and not monitor.is_alive:
                _logger.warning("EscapeMonitor 线程已死亡，退出等待")
                raise RuntimeError("EscapeMonitor thread died")
            text = monitor.get_queued_input()
            if text is not None:
                return text
            if deadline is not None and time.monotonic() >= deadline:
                return ""
            time.sleep(0.05)

    def setup_completion(self, monitor: _MonitorProtocol) -> None:
        monitor.set_completion_callback(self._components.cmpl_handler.on_tab)
        monitor.set_dismiss_completion_callback(self._components.cmpl_handler.on_dismiss)
        monitor.set_completion_navigate_callback(self._components.cmpl_handler.on_navigate)
        monitor.set_auto_completion_callback(self._components.cmpl_handler.on_auto)

    @property
    def bottom_bar(self):
        return self._components.bottom_bar

    @property
    def output_adapter(self):
        """获取当前 OutputAdapter 实例。"""
        return self._components.tui_renderer.output_adapter

    def set_panel_refresh_callback(self, callback: Callable[[], None] | None) -> None:
        self._components.engine.set_panel_refresh_callback(callback)

    def setup_bottom_bar(self) -> None:
        with render_lock:
            self._components.bottom_bar.setup()

    def teardown_bottom_bar(self) -> None:
        self._components.bottom_bar.teardown()

    def ensure_cursor_upper(self) -> None:
        self._components.engine.ensure_cursor_upper()

    def refresh_bottom_bar(self, text: str, cursor_pos: int = -1) -> None:
        effective_pos = len(text) if cursor_pos < 0 else cursor_pos
        # ★ 同步更新 Input 类中的文本副本（单一数据源策略）
        if hasattr(self._components, 'input') and self._components.input is not None:
            self._components.input.buffer.set_buffer(text)
        self._components.bottom_bar.set_input_state(text, effective_pos)
        self._components.engine.request_bottom_redraw()

    def flush(self, timeout: float | None = 5.0) -> None:
        self._components.engine.flush(timeout=timeout)

    def push_cmd(self, cmd: tuple) -> None:
        self._components.engine.push_cmd(cmd)
