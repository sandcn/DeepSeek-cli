"""ChatUIConsumer — 终端聊天消费者外观类。

通过组合 5 个子系统完成端到端渲染管线：
  EventDispatcher  → 事件订阅/过滤/入队
  ContentRenderer  → 14 种渲染命令执行
  RenderEngine     → Reader 线程 + 命令队列 + 渲染循环
  _RenderState     → 渲染器生命周期
  _CmplHandler     → Tab 补全交互
"""

from __future__ import annotations

import logging
import queue
import time
from typing import TYPE_CHECKING, ClassVar

from ..ui._bottom_bar import _BottomBar
from ..ui._completion import CompletionEngine
from ..ui.events.event_bus import DisplayEventBus
from ._completion import _CmplHandler
from ._const import RenderCommand
from ._dispatcher import EventDispatcher
from ._engine import RenderEngine
from ._render_state import _RenderState
from ._renderers import ContentRenderer
from ._state import (
    _active_consumer,
    _active_parallel_display,
    get_active_chat_ui,
)

if TYPE_CHECKING:
    from ..api.escape_monitor import EscapeMonitor

_logger = logging.getLogger(__name__)


class ChatUIConsumer:
    """消费 DisplayEventBus 事件，通过渲染命令队列驱动终端输出。

    Reader 线程以 10Hz 轮询命令队列，串行执行 render()
    进行终端 I/O。事件 handler 只在 EventBus 回调线程中做过滤+入队。

    内部子系统（通过组合协作）：
      _dispatcher (EventDispatcher)  — 事件订阅/过滤/入队
      _renderer   (ContentRenderer)  — 14 种渲染命令执行
      _engine     (RenderEngine)     — Reader 线程 + 命令队列 + 渲染循环
      _rs         (_RenderState)     — 渲染器生命周期管理
      _cmpl       (_CmplHandler)     — Tab 补全交互
    """

    # ── 向后兼容：旧版 _RENDER_DISPATCH（实际位于 ContentRenderer） ──
    # 测试代码通过 ChatUIConsumer._RENDER_DISPATCH 访问，保留此引用。
    _RENDER_DISPATCH: ClassVar = None  # type: ignore[assignment]

    def __init__(self, event_bus: DisplayEventBus | None = None):
        self._bus = event_bus or DisplayEventBus.get_default()

        # ── 渲染命令队列（线程安全） ──
        self._cmd_queue: queue.Queue = queue.Queue()

        # ── 子系统 ──
        self._rs = _RenderState()                              # 渲染器生命周期
        self._bottom_bar = _BottomBar()                        # 终端底部固定输入栏
        self._renderer = ContentRenderer(self._rs, self._bottom_bar)  # 渲染命令执行
        self._engine = RenderEngine(                            # Reader 线程 + 队列
            self._cmd_queue, self._renderer, self._bottom_bar,
            get_active_pd=lambda: _active_parallel_display,
        )
        self._dispatcher = EventDispatcher(self._bus, self._push_cmd)  # 事件订阅/过滤
        self._cmpl = _CmplHandler(                               # Tab 补全
            self._bottom_bar, CompletionEngine(),
        )

        self._started = False

    # ═══════════════════════════════════════════════════════
    # 生命周期
    # ═══════════════════════════════════════════════════════

    def start(self) -> None:
        """订阅事件 + 启动 reader 线程。幂等。"""
        if self._started:
            return

        # ★ 先订阅事件处理器，再设置活跃标记
        self._dispatcher.subscribe()

        global _active_consumer
        _active_consumer = self

        self._engine.start()
        self._started = True

    def stop(self) -> None:
        """取消订阅 + 停止 reader + 关闭渲染器 + 拆除底部栏。幂等。"""
        if not self._started:
            return

        # 1) 先停 reader
        self._engine.stop()

        # 2) 先清除活跃标记，再取消订阅
        global _active_consumer
        _active_consumer = None

        # 3) 取消订阅（reader 已停，不可能有新入队）
        self._dispatcher.unsubscribe()

        # 4) flush 残留命令
        self.flush()

        # 5) teardown 底部栏（锁保护）
        from ..ui._lock import output_lock
        with output_lock:
            self._bottom_bar.teardown()

        self._rs.close_all()
        self._started = False

    def suspend(self) -> None:
        """暂停渲染和终端设置，为交互式工具腾出终端。幂等。"""
        self._engine.suspend()
        from ..ui._lock import output_lock
        with output_lock:
            self._bottom_bar.teardown()

    def resume(self) -> None:
        """恢复渲染和终端设置。仅在已 start() 但 reader 已停止时有效。"""
        if not self._started:
            return

        from ..ui._lock import output_lock
        with output_lock:
            import shutil
            import sys
            height = shutil.get_terminal_size().lines
            sys.__stdout__.write(f"\033[{height};1H")
            sys.__stdout__.flush()
            self._bottom_bar.setup()
            self._engine.resume()

    # ═══════════════════════════════════════════════════════
    # 公开方法（线程安全：仅入队，不直接 I/O）
    # ═══════════════════════════════════════════════════════

    def on_user_message(self, text: str) -> None:
        """入队用户消息渲染命令。"""
        self._push_cmd((RenderCommand.USER_MSG, text))

    def on_notification(self, text: str) -> None:
        """入队系统通知渲染命令。"""
        self._push_cmd((RenderCommand.NOTIFICATION, text))

    def on_error(self, message: str) -> None:
        """入队系统错误渲染命令（红色 ◆ 样式）。线程安全：仅入队。"""
        if not message:
            return
        self._push_cmd((RenderCommand.ERROR, message))

    def refresh(self) -> None:
        """公开刷新接口 — 供外部程序/timer 定时调用以刷新 TUI。

        安全地从任何线程调用：自行管理 output_lock 获取与释放。
        """
        from ..ui._lock import _try_acquire_output_lock, output_lock

        # 1. ParallelDisplay 面板刷新（无锁）
        pd = _active_parallel_display
        if pd is not None:
            try:
                pd.refresh()
            except Exception:
                _logger.debug("refresh: ParallelDisplay 刷新异常", exc_info=True)
                self._push_cmd((RenderCommand.ERROR, "ParallelDisplay 刷新失败，请查看日志获取详情"))

        # 2. 终端尺寸检测
        with _try_acquire_output_lock(name="refresh.resize", timeout=1.0) as locked:
            resized = locked and self._bottom_bar.check_resize()

        # 3. 底部栏重绘 + 光标定位
        if resized or self._bottom_bar.is_status_active:
            with _try_acquire_output_lock(name="refresh.bottom", timeout=1.0) as locked:
                if locked:
                    self._bottom_bar.force_redraw()
                    self._engine._position_cursor()

    def write_line(self, text: str) -> None:
        """入队通用文本行渲染命令，走统一渲染管线。"""
        self._push_cmd((RenderCommand.WRITE_LINE, text))

    def display_messages(self, messages: list[dict], speed: int = 0) -> None:
        """入队消息列表渲染命令。"""
        self._push_cmd((RenderCommand.DISPLAY_MSGS, messages, speed))

    def wait_for_user_input(
        self, monitor: "EscapeMonitor", prefill: str = "",
        timeout: float | None = None,
    ) -> str:
        """通过底部栏等待用户输入（阻塞同步调用）。"""
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

    # ── Tab 补全 ───────────────────────────────────────

    def setup_completion(self, monitor: "EscapeMonitor") -> None:
        """注册补全回调到 EscapeMonitor。"""
        monitor.set_completion_callback(self._cmpl.on_tab)
        monitor.set_dismiss_completion_callback(self._cmpl.on_dismiss)
        monitor.set_completion_navigate_callback(self._cmpl.on_navigate)

    # ── 底部栏委托 ────────────────────────────────────

    def setup_bottom_bar(self) -> None:
        from ..ui._lock import output_lock
        with output_lock:
            self._bottom_bar.setup()

    def teardown_bottom_bar(self) -> None:
        self._bottom_bar.teardown()

    def ensure_cursor_upper(self) -> None:
        self._bottom_bar.ensure_cursor_in_upper()

    def ensure_cursor_lower(self) -> None:
        self._bottom_bar.ensure_cursor_in_lower()

    def refresh_bottom_bar(self, text: str, cursor_pos: int = -1) -> None:
        self._bottom_bar.refresh(text, cursor_pos=cursor_pos)

    def redraw_bottom_bar(self) -> None:
        self._bottom_bar.redraw()

    def enable_status_refresh(self) -> None:
        self._bottom_bar.enable_status()

    def disable_status_refresh(self) -> None:
        self._bottom_bar.disable_status()

    def get_status_elapsed(self) -> float:
        return self._bottom_bar.get_status_elapsed()

    def reset_tool_count(self) -> None:
        self._bottom_bar.reset_tool_count()

    def set_model_name(self, name: str) -> None:
        self._bottom_bar.set_model_name(name)

    def _push_cmd(self, cmd: tuple) -> None:
        self._cmd_queue.put(cmd)
        self._engine._cmd_event.set()

    def flush(self, timeout: float = 5.0) -> None:
        """阻塞等待所有待处理渲染命令执行完毕。"""
        self._engine.flush(timeout=timeout)

    # ── 向后兼容：代理到 EventDispatcher 的事件处理器 ──
    # 测试代码直接调用 consumer._on_model_phase(event) 等，
    # 提供代理方法保持 backward compat。

    def _on_model_phase(self, event) -> None:
        self._dispatcher._on_model_phase(event)

    def _on_reasoning_chunk(self, event) -> None:
        self._dispatcher._on_reasoning_chunk(event)

    def _on_content_chunk(self, event) -> None:
        self._dispatcher._on_content_chunk(event)

    def _on_phase_done(self, event) -> None:
        self._dispatcher._on_phase_done(event)

    def _on_tool_started(self, event) -> None:
        self._dispatcher._on_tool_started(event)

    def _on_tool_done(self, event) -> None:
        self._dispatcher._on_tool_done(event)

    def _on_tool_output(self, event) -> None:
        self._dispatcher._on_tool_output(event)

    def _on_tool_summary(self, event) -> None:
        self._dispatcher._on_tool_summary(event)

    def _on_parse_info(self, event) -> None:
        self._dispatcher._on_parse_info(event)

    def _on_parse_info_done(self, event) -> None:
        self._dispatcher._on_parse_info_done(event)

    def _on_output(self, event) -> None:
        self._dispatcher._on_output(event)


# ── 向后兼容：设置 _RENDER_DISPATCH ────────────────────
from ._renderers import _build_render_dispatch  # noqa: E402
ChatUIConsumer._RENDER_DISPATCH = _build_render_dispatch()
