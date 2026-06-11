"""chat_ui 消费者模块 — ChatUIConsumer 外观类，组合所有子系统。

Layer 4 — 组合 _state + _dispatcher + _renderers + _engine + _completion
          + _render_state，对外提供统一公开 API。
"""

from __future__ import annotations

import logging
import sys
import time
from typing import TYPE_CHECKING, Any, Callable

from ._completion import _CmplHandler
from ._const import _ANSI_CURSOR_BOTTOM, RenderCommand
from ._dispatcher import EventDispatcher
from ._engine import RenderEngine
from ._render_state import _RenderState
from ._renderers import ContentRenderer
from ..ui.tui._message_display import _display_messages
from ..ui._bottom_bar import _BottomBar
from ..ui._completion import CompletionEngine
from ..ui._cursor_tracker import CursorTracker
from ..ui.events.event_bus import DisplayEventBus
from rich.console import Console
from ..api.renderer.output import OutputAdapter
from ..terminal import get_safe_console_config

if TYPE_CHECKING:
    from ..api.escape_monitor import EscapeMonitor

_logger = logging.getLogger(__name__)


class ChatUIConsumer:
    """消费 DisplayEventBus 事件，通过渲染命令队列驱动终端输出。

    内部子系统：
      _rs     (_RenderState)   — 渲染器生命周期管理
      _cmpl   (_CmplHandler)   — Tab 补全交互
      _engine (RenderEngine)   — render 线程 + 命令队列 + 渲染循环
      _disp   (EventDispatcher)— 事件过滤+入队

    render 线程以 10Hz 轮询命令队列，串行执行 _render()
    进行终端 I/O。事件 handler 只在 EventBus 回调线程中做过滤+入队。
    """

    def __init__(self, event_bus: "DisplayEventBus | None" = None):
        self._bus = event_bus or DisplayEventBus.get_default()

        # ── 子系统（构造顺序：被依赖者先构造） ──
        self._rs = _RenderState()             # 渲染器生命周期管理

        # ★ 全局光标坐标追踪器（所有渲染子系统共享同一实例）
        self._cursor_tracker = CursorTracker()

        # ★ 终端底部固定输入栏（注入光标追踪器）
        self._bottom_bar = _BottomBar(cursor_tracker=self._cursor_tracker)

        # ★ 创建 OutputAdapter（由 ChatUIConsumer 构造，注入到 ContentRenderer）
        # 替代原来 ContentRenderer 内部创建 Console 和 OutputAdapter 的模式。
        # 关注点分离：ChatUIConsumer 负责依赖创建，ContentRenderer 只负责消费。
        console = Console(**get_safe_console_config(), file=sys.__stdout__)
        output_adapter = OutputAdapter(console)

        # ★ 渲染引擎（内部管理 queue + render 线程）
        # on_display_messages 回调注入：消除 ContentRenderer 对 tui._message_display 的直接 import
        # output_adapter 构造注入：消除 ContentRenderer 对 rich.Console 的运行时 import
        # cursor_tracker 注入：所有渲染子系统共享同一光标追踪实例
        self._renderer = ContentRenderer(
            self._rs, output_adapter, self._bottom_bar,
            on_display_messages=_display_messages,
            cursor_tracker=self._cursor_tracker,
        )
        self._engine = RenderEngine(
            self._renderer, self._bottom_bar,
            cursor_tracker=self._cursor_tracker,
        )

        # ★ 事件分发器（通过 engine.push_cmd 回调入队，解耦队列实现）
        self._disp = EventDispatcher(push_cmd=self._engine.push_cmd)

        # ★ 将 OutputAdapter 注入 _RenderState，使推理/内容渲染器共享同一实例
        #    替代每个 IncrementalRenderer 独立创建 Console+OutputAdapter 的模式。
        self._rs.set_output_adapter(output_adapter)

        self._cmpl = _CmplHandler(
            self._bottom_bar, CompletionEngine(),
            request_redraw=self._engine.request_bottom_redraw,
        )

        # ★ 预绑定事件处理器（用于 EventBus subscribe/unsubscribe）
        #    惰性绑定——在 start() 中首次使用时创建
        self._bound_handlers: dict[type, Any] | None = None

        self._started = False

    # ═══════════════════════════════════════════════════════
    # 生命周期
    # ═══════════════════════════════════════════════════════

    def start(self) -> None:
        """订阅事件 + 启动 render 线程。幂等。"""
        if self._started:
            return

        # ★ 惰性绑定事件处理器（仅在首次 start 时）
        if self._bound_handlers is None:
            self._bound_handlers = {}
            from ._dispatcher import _HANDLER_MAP
            for _, (event_type, handler_name) in _HANDLER_MAP.items():
                handler = getattr(self._disp, handler_name)
                self._bound_handlers[event_type] = handler

        # ★ 防御性取消旧订阅（防止多次 start/stop 后订阅泄漏）
        for event_type in self._bound_handlers:
            try:
                self._bus.unsubscribe(
                    self._bound_handlers[event_type], event_type=event_type,
                )
            except Exception:
                pass  # 未订阅时静默跳过

        # ★ 先订阅事件处理器，再设置活跃标记
        #    顺序保障：在 _active_consumer 被外界可见前，ChatUIConsumer
        #    已完整订阅 EventBus，消除 OutputEvent 在过渡期丢失的竞态窗口。
        for event_type in self._bound_handlers:
            self._bus.subscribe(self._bound_handlers[event_type], event_type=event_type)

        # ★ 设置模块级全局引用（引用计数封装在 _state 模块）
        from . import _state
        _state._register_consumer(self)

        self._engine.start()
        self._started = True

    def stop(self) -> None:
        """取消订阅 + 停止 render + 关闭渲染器 + 拆除底部栏。幂等。"""
        if not self._started:
            return

        # ★ 先取消订阅（防止新命令入队）
        if self._bound_handlers is not None:
            for event_type in self._bound_handlers:
                try:
                    self._bus.unsubscribe(
                        self._bound_handlers[event_type], event_type=event_type,
                    )
                except Exception:
                    _logger.debug(
                        "stop: unsubscribe %s 失败", event_type.__name__,
                        exc_info=True,
                    )

        # ★ 再 flush — 等待 render 线程消费完队列中所有待处理命令
        #    确保最终消息（如"再见"）被渲染到终端后再停止引擎。
        self._engine.flush()

        # ★ 最后停 render（内部 drain 残量队列，flush 后应为空）
        self._engine.stop()

        # 引用计数递减（封装在 _state 模块中）
        from . import _state
        _state._unregister_consumer()

        # 关闭渲染器 + teardown 底部栏（锁保护，与 suspend() 一致）
        from ..ui._lock import output_lock
        with output_lock:
            self._rs.close_all()
            self._bottom_bar.teardown()

        self._started = False

    def suspend(self) -> None:
        """暂停渲染和终端设置，为交互式工具腾出终端。幂等。"""
        if not self._started:
            return
        # ★ 先停 render（flush 不会造任务阻塞在空队列上），再 flush 剩余命令
        self._engine.stop()
        self._engine.flush()
        from ..ui._lock import output_lock
        with output_lock:
            self._bottom_bar.teardown()

    def resume(self) -> None:
        """恢复渲染和终端设置。仅在已 start() 但 render 已停止时有效。"""
        if not self._started:
            return
        # ★ 引擎已在运行则跳过（防止重复启动导致双 render 线程）
        if self._engine._render_running:
            return

        from ..ui._blessed import get_terminal
        from ..ui._lock import output_lock
        with output_lock:
            # ★ 将光标定位到终端末行，为 DECSTBM 设置做准备。
            try:
                term = get_terminal()
                sys.__stdout__.write(term.move_xy(0, term.height - 1))
            except Exception:
                # 回退：固定大行号 \033[9999;1H（终端自动 clamp）
                sys.__stdout__.write(_ANSI_CURSOR_BOTTOM)
            sys.__stdout__.flush()
            self._bottom_bar.setup()
            self._engine.start()

    # ═══════════════════════════════════════════════════════
    # 公开方法（线程安全：仅入队，不直接 I/O）
    # ═══════════════════════════════════════════════════════

    def on_user_message(self, text: str) -> None:
        """入队用户消息渲染命令。"""
        self._engine.push_cmd((RenderCommand.USER_MSG, text))

    def on_notification(self, text: str) -> None:
        """入队系统通知渲染命令。"""
        self._engine.push_cmd((RenderCommand.NOTIFICATION, text))

    def on_error(self, message: str) -> None:
        """入队系统错误渲染命令（红色 ◆ 样式）。

        由 ChatUIErrorHandler 在捕获 ERROR+ 级别日志时调用，
        也可由其他模块直接调用以显示运行时错误信息。

        线程安全：仅入队，不直接 I/O。
        """
        if not message:
            return
        self._engine.push_cmd((RenderCommand.ERROR, message))

    def refresh(self) -> None:
        """公开刷新接口 — 供外部程序/timer 定时调用以刷新 TUI。

        安全地从任何线程调用。
        底部栏重绘由 render 线程 _phase_redraw_bottom() 10Hz 轮询处理。
        """

    def request_bottom_redraw(self) -> None:
        """请求 render 线程重绘底部栏（线程安全）。

        通过 _engine.request_bottom_redraw() 设置 threading.Event 标志位，
        唤醒 render 线程在 _phase_redraw_bottom() 中触发 force_redraw()。
        """
        self._engine.request_bottom_redraw()

    def write_line(self, text: str) -> None:
        """入队通用文本行渲染命令，走统一渲染管线。"""
        self._engine.push_cmd((RenderCommand.WRITE_LINE, text))

    def display_messages(self, messages: list[dict], speed: int = 0) -> None:
        """入队消息列表渲染命令。"""
        self._engine.push_cmd((RenderCommand.DISPLAY_MSGS, messages, speed))

    def wait_for_user_input(
        self, monitor: "EscapeMonitor", prefill: str = "",
        timeout: float | None = None,
    ) -> str:
        """通过底部栏等待用户输入（阻塞同步调用）。

        参数:
            timeout: 超时秒数，None 表示无限等待。
                     超时后返回空字符串，避免 EscapeMonitor 故障时永久阻塞。
        """
        if prefill:
            monitor.set_prefill(prefill)
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            text = monitor.get_queued_input()
            if text is not None:
                # 任何字符串（含空字符串""）视为有效输入，None 表示无输入
                return text
            if deadline is not None and time.monotonic() >= deadline:
                return ""
            time.sleep(0.05)

    # ── Tab 补全 ───────────────────────────────────────

    def setup_completion(self, monitor: "EscapeMonitor") -> None:
        """注册补全回调到 EscapeMonitor（含 Tab 补全 + 自动弹出补全）。"""
        monitor.set_completion_callback(self._cmpl.on_tab)
        monitor.set_dismiss_completion_callback(self._cmpl.on_dismiss)
        monitor.set_completion_navigate_callback(self._cmpl.on_navigate)
        monitor.set_auto_completion_callback(self._cmpl.on_auto)

    # ── 底部栏 ────────────────────────────────────────

    @property
    def bottom_bar(self) -> "_BottomBar":
        """底部栏对象，直接访问底部栏的完整 API。

        对于 setup_bottom_bar / teardown_bottom_bar / refresh_bottom_bar /
        ensure_cursor_upper 等高频/含锁操作的方法，ChatUIConsumer 仍保留显式委托；
        其余底部栏操作（状态刷新、模型名、工具计数等）通过此属性直接访问。
        """
        return self._bottom_bar

    @property
    def output_adapter(self) -> "OutputAdapter":
        """OutputAdapter 对象 — 供外部模块获取统一的终端输出适配器。

        由 ChatUIConsumer 构造时创建，生命周期与 ChatUIConsumer 一致。
        """
        return self._renderer._adapter

    def set_panel_refresh_callback(
        self, callback: Callable[[], None] | None,
    ) -> None:
        """设置面板刷新回调，委托给 RenderEngine。

        由 ParallelDisplay 在 start() 中注册，使得 render 线程
        的 10Hz 周期可以驱动 SubAgent 面板刷新。

        Args:
            callback: 无参回调，或 None 来注销。
        """
        self._engine.set_panel_refresh_callback(callback)

    def setup_bottom_bar(self) -> None:
        from ..ui._lock import output_lock
        with output_lock:
            self._bottom_bar.setup()

    def teardown_bottom_bar(self) -> None:
        self._bottom_bar.teardown()

    def ensure_cursor_upper(self) -> None:
        """将光标移到内容区。调用方须持有 output_lock。"""
        self._engine.ensure_cursor_upper()

    def refresh_bottom_bar(self, text: str, cursor_pos: int = -1) -> None:
        """刷新底部栏输入区（线程安全：仅更新状态 + 请求重绘）。

        设置输入文本和光标位置后，调用 _engine.request_bottom_redraw()
        设置 threading.Event 标志位并唤醒 render 线程，
        在 _phase_redraw_bottom() 中执行 force_redraw()，
        避免在 EscapeMonitor 回调线程中直接写终端。

        Args:
            text: 当前输入文本。
            cursor_pos: 光标在输入文本中的偏移，-1 表示文本末尾。
        """
        effective_pos = len(text) if cursor_pos < 0 else cursor_pos
        self._bottom_bar.set_input_state(text, effective_pos)
        self._engine.request_bottom_redraw()

    def flush(self, timeout: float | None = 5.0) -> None:
        """阻塞等待所有待处理渲染命令执行完毕。（委托 _engine）"""
        self._engine.flush(timeout=timeout)

    def push_cmd(self, cmd: tuple) -> None:
        """向渲染命令队列入队（线程安全）。

        由 ParallelDisplay 等外部模块使用，委托给 RenderEngine.push_cmd()。
        """
        self._engine.push_cmd(cmd)
