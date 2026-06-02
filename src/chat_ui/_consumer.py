"""chat_ui 消费者模块 — ChatUIConsumer 外观类，组合所有子系统。

Layer 4 — 组合 _state + _dispatcher + _renderers + _engine + _completion
          + _render_state，对外提供统一公开 API。
"""

from __future__ import annotations

import logging
import sys
import time
from typing import TYPE_CHECKING, Any

from ._completion import _CmplHandler
from ._const import _ANSI_CURSOR_BOTTOM, RenderCommand
from ._dispatcher import EventDispatcher
from ._engine import RenderEngine
from ._render_state import _RenderState
from ._renderers import ContentRenderer
from ..ui.tui._message_display import _display_messages

if TYPE_CHECKING:
    from ..api.escape_monitor import EscapeMonitor
    from ..ui.events.event_bus import DisplayEventBus

_logger = logging.getLogger(__name__)


class ChatUIConsumer:
    """消费 DisplayEventBus 事件，通过渲染命令队列驱动终端输出。

    内部子系统：
      _rs     (_RenderState)   — 渲染器生命周期管理
      _cmpl   (_CmplHandler)   — Tab 补全交互
      _engine (RenderEngine)   — Reader 线程 + 命令队列 + 渲染循环
      _disp   (EventDispatcher)— 事件过滤+入队

    Reader 线程以 10Hz 轮询命令队列，串行执行 _render()
    进行终端 I/O。事件 handler 只在 EventBus 回调线程中做过滤+入队。
    """

    def __init__(self, event_bus: "DisplayEventBus | None" = None):
        from ..ui.events.event_bus import DisplayEventBus
        self._bus = event_bus or DisplayEventBus.get_default()

        # ── 子系统（构造顺序：被依赖者先构造） ──
        self._rs = _RenderState()             # 渲染器生命周期管理
        from ..ui._bottom_bar import _BottomBar
        self._bottom_bar = _BottomBar()       # 终端底部固定输入栏

        # ★ 渲染引擎（内部管理 queue + reader 线程）
        # on_display_messages 回调注入：消除 ContentRenderer 对 tui._message_display 的直接 import
        self._renderer = ContentRenderer(
            self._rs, self._bottom_bar,
            on_display_messages=_display_messages,
        )
        self._engine = RenderEngine(self._renderer, self._bottom_bar)

        # ★ 事件分发器（通过 engine.push_cmd 回调入队，解耦队列实现）
        self._disp = EventDispatcher(push_cmd=self._engine.push_cmd)

        from ..ui._completion import CompletionEngine
        self._cmpl = _CmplHandler(
            self._bottom_bar, CompletionEngine(),
        )

        # ★ 预绑定事件处理器（用于 EventBus subscribe/unsubscribe）
        #    惰性绑定——在 start() 中首次使用时创建
        self._bound_handlers: dict[type, Any] | None = None

        self._started = False

    # ═══════════════════════════════════════════════════════
    # 生命周期
    # ═══════════════════════════════════════════════════════

    def start(self) -> None:
        """订阅事件 + 启动 reader 线程。幂等。"""
        if self._started:
            return

        # ★ 惰性绑定事件处理器（仅在首次 start 时）
        if self._bound_handlers is None:
            self._bound_handlers = {}
            for type_name, _handler_name in EventDispatcher._EVENT_HANDLERS:
                event_type = self._disp._get_event_type(type_name)
                handler = getattr(self._disp, _handler_name)
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

        # ★ 设置模块级全局引用（通过 _state 模块，引用计数防竞态）
        from . import _state
        _state._active_consumer_refcount += 1
        _state._active_consumer = self

        self._engine.start()
        self._started = True

    def stop(self) -> None:
        """取消订阅 + 停止 reader + 关闭渲染器 + 拆除底部栏。幂等。"""
        if not self._started:
            return

        # 1) 先停 reader（与 suspend() 顺序一致）
        self._engine.stop()

        # 2) 引用计数递减，归零时清除全局引用（防多实例竞态）
        from . import _state
        _state._active_consumer_refcount -= 1
        try:
            if _state._active_consumer_refcount <= 0:
                _state._active_consumer = None
        except TypeError:
            # 兼容测试 mock 场景：MagicMock 不支持 int <= 比较，直接清空
            _state._active_consumer = None

        # 3) 取消订阅（reader 已停，不可能有新入队）
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

        # 4) flush 残留命令
        self._engine.flush()

        # 5) teardown 底部栏（锁保护，与 suspend() 一致）
        from ..ui._lock import output_lock
        with output_lock:
            self._bottom_bar.teardown()

        self._rs.close_all()
        self._started = False

    def suspend(self) -> None:
        """暂停渲染和终端设置，为交互式工具腾出终端。幂等。"""
        if not self._started:
            return
        # ★ 先停 reader（flush 不会造任务阻塞在空队列上），再 flush 剩余命令
        self._engine.stop()
        self._engine.flush()
        from ..ui._lock import output_lock
        with output_lock:
            self._bottom_bar.teardown()

    def resume(self) -> None:
        """恢复渲染和终端设置。仅在已 start() 但 reader 已停止时有效。"""
        if not self._started:
            return
        # ★ 引擎已在运行则跳过（防止重复启动导致双 reader 线程）
        if self._engine._reader_running:
            return

        from ..ui._lock import output_lock
        with output_lock:
            # ★ 用固定大行号 \033[9999;1H 将光标定位到终端末行（终端自动 clamp），
            #   为 DECSTBM 设置做准备。
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

        安全地从任何线程调用：自行管理 output_lock 获取与释放。
        执行以下刷新操作：
          1. ParallelDisplay 面板刷新（若有活跃实例）
          2. 底部栏重绘（force_redraw）
          3. 光标定位（position_cursor）

        与 _drain_queue 不同：不消费命令队列，专供外部定时刷新。
        """
        from ..ui._lock import _try_acquire_output_lock

        # ★ 1. ParallelDisplay 面板刷新（无锁，内部自行用 timeout try-lock）
        from . import _active_parallel_display as _apd
        pd = _apd
        if pd is not None:
            try:
                pd.refresh()
            except Exception:
                _logger.debug("refresh: ParallelDisplay 刷新异常", exc_info=True)
                self._engine.push_cmd((RenderCommand.ERROR, "ParallelDisplay 刷新失败，请查看日志获取详情"))

        # ★ 2. 底部栏重绘 + 光标定位（有活跃状态时执行）
        if self._bottom_bar.is_status_active:
            with _try_acquire_output_lock(name="refresh.bottom", timeout=1.0) as locked:
                if locked:
                    self._bottom_bar.force_redraw()
                    self._engine.position_cursor()

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
            if text:
                # 非空字符串视为有效输入
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
        """刷新底部栏输入区并定位光标到输入行。

        force_redraw 之后立即调用 ensure_cursor_in_lower 将光标定位到
        输入行，避免光标停留在上屏（内容区末行）。空闲期 Reader 线程
        快速跳过时不执行 position_cursor()，必须在此路径显式定位光标。
        """
        from ..ui._lock import output_lock
        with output_lock:
            self._bottom_bar._last_text = text
            self._bottom_bar._input_cursor_pos = len(text) if cursor_pos < 0 else cursor_pos
            self._bottom_bar.force_redraw()
            # ensure_cursor_in_lower() + flush：将光标定位到输入行。
            # force_redraw 的 \0338 将光标恢复到上屏；ensure_cursor_in_lower()
            # 写入的 ANSI 序列需显式 flush（stdout 行缓冲，无 \n 不自动提交）。
            self._bottom_bar.ensure_cursor_in_lower()
            sys.__stdout__.flush()

    def flush(self, timeout: float | None = 5.0) -> None:
        """阻塞等待所有待处理渲染命令执行完毕。（委托 _engine）"""
        self._engine.flush(timeout=timeout)
