"""消费者 API — ChatUIConsumer 兼容实现（精简版）。

使用新基础设施模块（_renderer.py / _bottom_bar.py / _input.py / _screen.py /
_const.py / _config.py），直接持有子系统实例而非通过 _ChatUIComponents 中间容器。

关键改动：
  - 取消 ``_components``（``_ChatUIComponents``）中间容器，改为直接持有子组件
  - ``start()`` 移除 ``register_tui_styles()`` 调用
  - ``resume()`` 使用 ``_screen.cursor_goto()`` 替代 ``get_terminal().move_xy()``
  - 组件装配在 ``_assemble()`` 中直接完成（替代 factory.py）

设计模式: 外观（Facade）— ChatUIConsumer 作为所有子系统的统一协调入口。
"""

from __future__ import annotations

import logging
import sys
import threading
import time
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

from src.tui._const import RenderCommand
from src.tui.state.consumer_registry import (
    _register_consumer,
    _unregister_consumer,
    get_active_chat_ui,
)
from src.tui._locks import render_lock

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# 向后兼容的组件命名空间
# ═══════════════════════════════════════════════════════════

class _ComponentsNamespace:
    """向后兼容的组件命名空间，替代旧的 ``_ChatUIComponents``。

    仅暴露外部调用方需要的属性（``input``），其余通过 ``ChatUIConsumer``
    的直接属性或公开方法访问。

    Attributes:
        input: 统一输入管理实例。
    """
    __slots__ = ('input',)

    def __init__(self, input_instance: "Input | None" = None):
        self.input = input_instance


# ═══════════════════════════════════════════════════════════
# ChatUIConsumer — 对外公开 API（精简版）
# ═══════════════════════════════════════════════════════════

class ChatUIConsumer:
    """终端聊天消费者 — 精简版 TUI 架构。

    直接持有子系统实例：

    - ``_rs`` (ChatRenderState) — 渲染器生命周期
    - ``_engine`` (TuiEngine) — render 线程 + 命令队列
    - ``_bb`` (_BottomBar) — 底部固定输入栏
    - ``_dispatcher`` (EventDispatcher) — 事件过滤+入队
    - ``_renderer`` (TuiRenderer) — 渲染分发
    - ``_cmpl_handler`` (_CmplHandler) — Tab 补全交互
    - ``_input`` (Input) — 统一输入管理
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

        # ── 直接装配子系统（替代旧 factory.py） ──
        self._assemble()

        self._bound_handlers: dict[type, Callable] | None = None
        self._state_lock = threading.Lock()
        self._started = False
        self._handlers_bound = False

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
        instance._bound_handlers = None
        instance._state_lock = threading.Lock()
        instance._started = False
        instance._handlers_bound = False

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

        # 向后兼容的 _components 属性
        instance._components = _ComponentsNamespace(instance._input)
        return instance

    # ── 内部装配 ──────────────────────────────────

    def _assemble(self) -> None:
        """直接装配子系统（替代旧 ``factory.py`` 的 ``_create_chat_ui_components``）。

        分两步：
          1. 框架基础设施（Console + OutputAdapter）
          2. 聊天域子系统（ChatRenderState + _BottomBar + Input + TuiEngine +
             TuiRenderer + EventDispatcher + _CmplHandler）
        """
        from src.tui._bottom_bar import _BottomBar
        from src.tui._input import Input
        from src.tui._renderer import TuiEngine, TuiRenderer, EventDispatcher
        from src.tui._completion import _CmplHandler
        from src.tui.state.render_state import ChatRenderState
        from src.tui.consumer.chat_config import ChatConfig
        from src.tui._cursor_tracker import CursorTracker
        from src.tui._completion_engine import CompletionEngine
        from src.config.defaults import INPUT_HISTORY_FILE
        from rich.console import Console
        from src.renderer.output import OutputAdapter
        from src.terminal import get_safe_console_config

        # ── 框架基础设施 ──
        console = Console(**get_safe_console_config(), file=sys.__stdout__)
        output_adapter = OutputAdapter(console)

        # ── 聊天域子系统 ──
        self._rs: "ChatRenderState" = ChatRenderState()
        cursor_tracker = CursorTracker()
        self._bb: "_BottomBar" = _BottomBar(cursor_tracker=cursor_tracker)

        # ── 统一输入管理 ──
        self._input: "Input" = Input(
            fd=sys.stdin.fileno(),
            history_file=INPUT_HISTORY_FILE,
            cursor_tracker=cursor_tracker,
        )
        self._bb.set_input(self._input)

        # ── 框架组件 ──
        self._renderer: "TuiRenderer" = TuiRenderer(
            self._rs, output_adapter, self._bb,
            on_display_messages=self._display_messages_handler,
            cursor_tracker=cursor_tracker,
        )
        self._engine: "TuiEngine" = TuiEngine(
            self._renderer, self._bb,
            cursor_tracker=cursor_tracker,
            input_instance=self._input,
        )

        # ── 聊天域装配 ──
        self._dispatcher: "EventDispatcher" = EventDispatcher(
            push_cmd=self._engine.push_cmd,
            config=ChatConfig.defaults(),
        )
        self._rs.set_output_adapter(output_adapter)
        self._cmpl_handler: "_CmplHandler" = _CmplHandler(
            self._bb, CompletionEngine(),
            request_redraw=self._engine.request_bottom_redraw,
        )

        # 连接 SIGWINCH 重绘回调（resize 时触发底部栏重绘和光标重定位）
        self._bb.set_request_redraw_cb(self._engine.request_bottom_redraw)

        # ── 向后兼容的 _components 属性 ──
        self._components = _ComponentsNamespace(self._input)

    # ── 生命周期 ──────────────────────────────────

    def start(self) -> None:
        """启动 ChatUI 消费者。

        订阅 12 种 DisplayEvent；首次启动时跳过防御性 unsubscribe，
        后续重新启动时先 subscribe 新 handler 再 unsubscribe 旧 handler，
        消除事件丢失的时序窗口。

        Thread safety: ``_started`` 读写由 ``_state_lock`` 保护。
        """
        with self._state_lock:
            if self._started:
                return
            if self._bound_handlers is None:
                self._bound_handlers = {}
                for event_type, handler in self._dispatcher.list_handlers().items():
                    self._bound_handlers[event_type] = handler
            if self._handlers_bound:
                for event_type in self._bound_handlers:
                    try:
                        self._bus.unsubscribe(
                            self._bound_handlers[event_type], event_type=event_type,
                        )
                    except Exception:
                        _logger.debug(
                            "start: unsubscribe %s 失败",
                            event_type.__name__, exc_info=True,
                        )
            for event_type in self._bound_handlers:
                self._bus.subscribe(
                    self._bound_handlers[event_type], event_type=event_type,
                )
            self._handlers_bound = True
            _register_consumer(self)
            # ★ _engine.start() 在锁内调用：确保 daemon 线程创建时 _started 已设置，
            #    消除 stop() 在 start() 设置 _started 前通过锁检查的竞态窗口。
            #    _engine.start() 仅创建 daemon 线程并 start，不阻塞，锁内调用安全。
            self._engine.start()
            # ── 展示启动品牌屏 ──
            self._engine.push_cmd((RenderCommand.SPLASH,))
            self._started = True

    def stop(self) -> None:
        """停止 ChatUI 消费者。

        取消所有事件订阅、排空命令队列、停止渲染引擎、
        注销活跃消费者、清理渲染状态和底部栏。

        Thread safety: ``_started`` 读写由 ``_state_lock`` 保护。
        """
        with self._state_lock:
            if not self._started:
                return
            if self._bound_handlers is not None:
                for event_type in self._bound_handlers:
                    try:
                        self._bus.unsubscribe(
                            self._bound_handlers[event_type], event_type=event_type,
                        )
                    except Exception:
                        _logger.debug(
                            "stop: unsubscribe %s 失败",
                            event_type.__name__, exc_info=True,
                        )
            self._engine.flush()
            self._engine.stop()
            _unregister_consumer()
            with render_lock:
                self._rs.close_all()
                self._bb.teardown()
            self._started = False
            self._bound_handlers = None

    def suspend(self) -> None:
        """暂停渲染引擎，供交互式工具独占终端。

        停止 render 线程并拆除底部栏，释放终端控制权。
        必须已启动（``_started = True``）才有效。

        Thread safety: ``_started`` 检查由 ``_state_lock`` 保护。
        """
        with self._state_lock:
            if not self._started:
                return
            self._engine.flush()
            self._engine.stop()
            with render_lock:
                self._bb.teardown()

    def resume(self) -> None:
        """恢复渲染引擎，重建底部栏。

        使用 ``_screen.cursor_goto()`` 替代 blessed ``get_terminal().move_xy()``。

        Thread safety: ``_started`` 检查由 ``_state_lock`` 保护。
        """
        with self._state_lock:
            if not self._started:
                return
            if self._engine._render_running:
                return
            with render_lock:
                try:
                    from src.tui._screen import _get_terminal_size
                    _, height = _get_terminal_size()
                    sys.__stdout__.write(cursor_goto(height, 1))
                except Exception:
                    _logger.debug(
                        "resume 光标定位失败, 使用 ANSI 回退", exc_info=True,
                    )
                    sys.__stdout__.write("\033[9999;1H")
                sys.__stdout__.flush()
                self._bb._active = False
                self._bb.setup()
                self._engine.start()

    # ── 公开方法 ──────────────────────────────────

    def on_user_message(self, text: str) -> None:
        self._engine.push_cmd((RenderCommand.USER_MSG, text))

    def on_notification(self, text: str) -> None:
        self._engine.push_cmd((RenderCommand.NOTIFICATION, text))

    def on_error(self, message: str) -> None:
        if not message:
            return
        self._engine.push_cmd((RenderCommand.ERROR, message))

    def register_event_handler(self, event_type: type, handler_method: Callable) -> None:
        """注册自定义事件处理器（委托给 EventDispatcher）。

        允许外部代码在 ChatUIConsumer 启动前/后动态注册额外的事件映射。
        注册的处理器会在 ``start()`` 订阅事件总线时自动包含。

        Args:
            event_type: DisplayEvent 子类。
            handler_method: 事件处理 callable，签名为 ``(event) -> None``。
        """
        self._dispatcher.register_handler(event_type, handler_method)
        # 如果已经启动且已绑定，立即订阅新处理器
        with self._state_lock:
            if self._started and self._handlers_bound:
                self._bus.subscribe(handler_method, event_type=event_type)
                if self._bound_handlers is not None:
                    self._bound_handlers[event_type] = handler_method

    def _display_messages_handler(self, messages: list[dict], speed: int) -> None:
        """渲染消息列表到上屏区域（on_display_messages 回调）。

        Args:
            messages: 消息列表（已过滤 system 角色）。
            speed: 渲染速度（0=立即，>0=逐条渐显）。
        """
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
            self._engine.push_cmd((RenderCommand.WRITE_LINE, line))
        # 最后推入分隔线
        self._engine.push_cmd((RenderCommand.WRITE_LINE, f"  {DIM}{'─' * 40}{RESET}"))
        self._engine.flush()

    def request_bottom_redraw(self) -> None:
        self._engine.request_bottom_redraw()

    def write_line(self, text: str) -> None:
        self._engine.push_cmd((RenderCommand.WRITE_LINE, text))

    def display_messages(self, messages: list[dict], speed: int = 0) -> None:
        self._engine.push_cmd((RenderCommand.DISPLAY_MSGS, messages, speed))

    def wait_for_user_input(
        self, monitor, prefill: str = "", timeout: float | None = None,
        input_=None,
    ) -> str:
        """阻塞等待用户通过 Input 实例输入文本。

        轮询 ``input_.get_queued_input()``，以 50ms 间隔检查。

        Args:
            monitor: EscapeMonitor 实例，用于 is_alive 存活检测。
            prefill: 预填充文本（可选）。
            timeout: 超时秒数，None 表示无限等待。
            input_: 统一输入管理实例（Input 门面类）。

        Returns:
            用户输入文本；超时时返回空字符串 ``""``。
        """
        if input_ is None:
            input_ = self._input

        if prefill:
            if not monitor.is_alive:
                raise RuntimeError("EscapeMonitor thread died")
            _logger.debug(
                "wait_for_user_input: set prefill, len=%d", len(prefill),
            )
            input_.set_buffer(prefill)
            input_.echo(prefill)
            _logger.debug("wait_for_user_input: prefill done, entering poll loop")
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            if not monitor.is_alive:
                _logger.warning("EscapeMonitor 线程已死亡，退出等待")
                raise RuntimeError("EscapeMonitor thread died")
            text = input_.get_queued_input()
            if text is not None:
                return text
            if deadline is not None and time.monotonic() >= deadline:
                return ""
            time.sleep(0.05)

    def setup_completion(self, input_) -> None:
        """设置 Tab 补全回调到 Input 实例。

        Args:
            input_: 统一输入管理实例（Input 门面类）。
        """
        input_.set_completion_callback(self._cmpl_handler.on_tab)
        input_.set_dismiss_completion_callback(self._cmpl_handler.on_dismiss)
        input_.set_completion_navigate_callback(self._cmpl_handler.on_navigate)
        input_.set_auto_completion_callback(self._cmpl_handler.on_auto)

    @property
    def input(self):
        """获取 Input 实例。"""
        return self._input

    @property
    def bottom_bar(self):
        return self._bb

    @property
    def output_adapter(self):
        """获取当前 OutputAdapter 实例。"""
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
