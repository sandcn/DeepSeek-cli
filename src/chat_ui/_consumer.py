"""chat_ui 消费者模块 — ChatUIConsumer 外观类，组合所有子系统。

Layer 4 — 组合 _state + _dispatcher + _renderers + _engine + _completion
          + _render_state，对外提供统一公开 API。
"""

from __future__ import annotations

import logging
import shutil
import sys
import time
from typing import TYPE_CHECKING, Any, ClassVar

from ._completion import _CmplHandler
from ._const import _READER_INTERVAL, RenderCommand, _build_render_dispatch
from ._dispatcher import EventDispatcher
from ._engine import RenderEngine
from ._render_state import _RenderState
from ._renderers import ContentRenderer

if TYPE_CHECKING:
    from ..api.escape_monitor import EscapeMonitor
    from ..ui.events.event_bus import DisplayEventBus
    from ..ui.events.event_types import (
        ContentChunkEvent,
        ModelPhaseEvent,
        OutputEvent,
        ParseInfoDoneEvent,
        ParseInfoEvent,
        PhaseDoneEvent,
        ReasoningChunkEvent,
        ToolDoneEvent,
        ToolOutputChunkEvent,
        ToolStartedEvent,
        ToolSummaryEvent,
    )
    from ..ui.parallel.display import ParallelDisplay

_logger = logging.getLogger(__name__)

# ── 导入 _error_handler 触发副作用注册 ────────────────
# ChatUIErrorHandler 的模块级注册（logging.getLogger().addHandler）
# 在 import src.chat_ui 时即生效，拆分后保持此行为。
from . import _error_handler  # noqa: F401 — 触发 root logger handler 注册


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

    # ── 事件处理器注册表（start/stop 复用） ──
    _EVENT_HANDLER_NAMES: ClassVar[tuple[tuple[str, str], ...]] = EventDispatcher._EVENT_HANDLERS

    def __init__(self, event_bus: "DisplayEventBus | None" = None):
        from ..ui.events.event_bus import DisplayEventBus
        self._bus = event_bus or DisplayEventBus.get_default()

        # ── 子系统（构造顺序：被依赖者先构造） ──
        self._rs = _RenderState()             # 渲染器生命周期管理
        from ..ui._bottom_bar import _BottomBar
        self._bottom_bar = _BottomBar()       # 终端底部固定输入栏

        # ★ 渲染引擎（内部管理 queue + reader 线程）
        self._renderer = ContentRenderer(self._rs, self._bottom_bar)
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
    # 向后兼容属性（委托给子系统）
    # ═══════════════════════════════════════════════════════

    # _RENDER_DISPATCH — 类级别保留，供测试和调试使用
    _RENDER_DISPATCH: ClassVar[dict[int, tuple[str, tuple[int, ...]]]] = _build_render_dispatch()

    @property
    def _cmd_queue(self):
        """向后兼容：委托给 _engine._cmd_queue（供测试直接访问）。"""
        return self._engine._cmd_queue

    def _on_model_phase(self, event) -> None:
        """向后兼容：委托给 EventDispatcher._on_model_phase（供测试直接调用）。"""
        self._disp._on_model_phase(event)

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
            for type_name, _handler_name in self._EVENT_HANDLER_NAMES:
                event_type = self._disp._get_event_type(type_name)
                handler = getattr(self._disp, _handler_name)
                self._bound_handlers[event_type] = handler

        # ★ 先订阅事件处理器，再设置活跃标记
        #    顺序保障：在 _active_consumer 被外界可见前，ChatUIConsumer
        #    已完整订阅 EventBus，消除 OutputEvent 在过渡期丢失的竞态窗口。
        for event_type in self._bound_handlers:
            self._bus.subscribe(self._bound_handlers[event_type], event_type=event_type)

        # ★ 设置模块级全局引用（通过 _state 模块）
        from . import _state
        _state._active_consumer = self

        self._engine.start()
        self._started = True

    def stop(self) -> None:
        """取消订阅 + 停止 reader + 关闭渲染器 + 拆除底部栏。幂等。"""
        if not self._started:
            return

        # 1) 先停 reader（与 suspend() 顺序一致）
        self._engine.stop()

        # 2) 先清除活跃标记，再取消订阅
        from . import _state
        _state._active_consumer = None

        # 3) 取消订阅（reader 已停，不可能有新入队）
        if self._bound_handlers is not None:
            for event_type in self._bound_handlers:
                self._bus.unsubscribe(self._bound_handlers[event_type], event_type=event_type)

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
            height = shutil.get_terminal_size().lines
            sys.__stdout__.write(f"\033[{height};1H")
            sys.__stdout__.flush()
            self._bottom_bar.setup()
            # 重新启动引擎
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
          2. 终端尺寸检测（check_resize）
          3. 底部栏重绘（force_redraw）
          4. 光标定位（_position_cursor）

        与 _drain_queue 不同：不消费命令队列，专供外部定时刷新。
        ParallelDisplay 面板刷新不持锁（内部自行用 try-lock 保护），
        尺寸检测与底部栏重绘用独立 output_lock 分步串行化。
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

        # ★ 2. 终端尺寸检测
        with _try_acquire_output_lock(name="refresh.resize", timeout=1.0) as locked:
            resized = locked and self._bottom_bar.check_resize()

        # ★ 3. 底部栏重绘 + 光标定位（有活跃状态或尺寸变化时执行）
        if resized or self._bottom_bar.is_status_active:
            with _try_acquire_output_lock(name="refresh.bottom", timeout=1.0) as locked:
                if locked:
                    self._bottom_bar.force_redraw()
                    self._engine._position_cursor()

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

    # ── 底部栏 ────────────────────────────────────────

    def setup_bottom_bar(self) -> None:
        from ..ui._lock import output_lock
        with output_lock:
            self._bottom_bar.setup()

    def teardown_bottom_bar(self) -> None:
        self._bottom_bar.teardown()

    def ensure_cursor_upper(self) -> None:
        """将光标移到内容区。调用方须持有 output_lock。"""
        self._engine.ensure_cursor_upper()

    def ensure_cursor_lower(self) -> None:
        """将光标移到输入行。调用方须持有 output_lock。"""
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
        """设置当前模型名字，更新底部栏状态行。"""
        self._bottom_bar.set_model_name(name)

    def flush(self, timeout: float | None = 5.0) -> None:
        """阻塞等待所有待处理渲染命令执行完毕。（委托 _engine）"""
        self._engine.flush(timeout=timeout)
