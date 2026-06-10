"""
并行 Agent 显示 — Claude Code 风格（ChatUI 驱动版）

职责分层：
  - ParallelDisplay：生命周期控制 + 状态代理 + 刷新调度
  - FrameRenderer：纯函数渲染（state → 行列表）

渲染路径：ParallelDisplay → FrameRenderer → OutputAdapter 直接写入。
不再依赖 chat_ui Control 体系和 SUBAGENT_REFRESH 命令队列。

2026-06-10 重构：
  - 移除 SubAgentPanelControl 依赖
  - 面板帧直接通过 OutputAdapter 写入，不经过 chat_ui 命令队列
  - 移除 _active_subagent_panel 全局引用
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from ..output_target import IOutputTarget, TerminalTarget
from ..renderer import FrameRenderer
from ..events.event_bus import DisplayEventBus
from ..events.event_types import LiveOutputEvent
from ._config import DisplayConfig
from ..base_display import BaseDisplay
from ..common.state_store import AgentStateStore
from ..terminal_adapter import (
    register_sigwinch_callback,
    unregister_sigwinch_callback,
    TerminalAdapter,
)

# ── 常量 ────────────────────────────────────────────────

_EVENTBUS_THROTTLE = 0.3   # 300ms — EventBus 发布频率阈值，防止高频 update 路径过度发布
_REFRESH_INTERVAL = 0.1  # 100ms — 帧刷新节流间隔（10Hz，与 ChatUI render 线程一致）
_ELAPSED_TICKER_INTERVAL = 0.5  # 500ms — 耗时（elapsed time）定时刷新间隔（2Hz）
_DEFAULT_HISTORY = 3
_logger = logging.getLogger(__name__)


class _DiffGuard:
    """diff_active 上下文管理器 — 通过 OutputAdapter 直接控制。

    职责：在 diff 输出期间清除并阻止面板渲染，
    输出完成后恢复面板渲染。
    """

    def __init__(self, display: "ParallelDisplay", capture_frame: bool):
        self._display = display
        self._capture_frame = capture_frame

    def __enter__(self):
        d = self._display
        if self._capture_frame and d._last_lines > 0:
            d._clear_frame_lines()

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False


class ParallelDisplay(BaseDisplay):
    """并行 Agent 实时显示管理器 — 直接渲染版（代理层）

    职责：
    1. 生命周期控制（start/stop）
    2. 状态更新代理（代理到 AgentStateStore）
    3. 面板刷新调度（直接通过 OutputAdapter 渲染帧）
    4. 特殊输出（capture_and_print/print_output）

    帧渲染通过 OutputAdapter 直接写入终端，不经过 chat_ui 命令队列。
    output_lock 由 OutputAdapter 内部自行管理。
    """

    def __init__(self, max_history: int = _DEFAULT_HISTORY,
                 output_target: IOutputTarget | None = None):
        super().__init__(output_target=output_target)
        self._store = AgentStateStore()
        self._terminal = output_target or TerminalTarget()
        self._started = False
        self._finished = False
        self._stopped = False
        self._last_eventbus_time: float = 0.0  # EventBus 上次发布时间戳
        self._last_refresh_time: float = 0.0  # _schedule_refresh 上次渲染时间戳

        # 根据终端宽度确定显示深度
        display_config = DisplayConfig(self._terminal.terminal_width)
        self.max_history = max_history or display_config.max_tool_history_items

        # 初始化渲染器
        self._renderer = FrameRenderer(
            terminal_width=self._terminal.terminal_width,
            frame=0,
            max_history=self.max_history,
        )

        # OutputAdapter（由 start() 中从 ChatUIConsumer 获取）
        self._adapter = None

        # 帧状态
        self._frame: int = 0
        self._last_lines: int = 0
        self._last_rendered_version: int = 0
        # DECSTBM 滚动区域底部行号（由 start() 从 chat_ui bottom_bar 获取）
        self._scroll_end: int = 0
        # 缩放刷新标记（信号安全：在 _on_resize 中设置，_schedule_refresh 中消费）
        self._needs_resize_refresh: bool = False

        # 后台耗时定时刷新任务（在 start() 中创建，stop() 中取消）
        self._refresh_task: asyncio.Task | None = None

        # stdout 捕获锁
        self._capture_lock = asyncio.Lock()

    # ── 终端缩放回调 ────────────────────────────────────

    def _on_resize(self, width: int, height: int) -> None:
        """终端缩放回调：重建 DisplayConfig + 刷新宽度 + 设置缩放刷新标记。

        信号安全约束（terminal_adapter._handle_sigwinch 禁止获取锁）：
        不在此处调用 _schedule_refresh() 或任何 I/O/锁操作，
        仅设置标记 _needs_resize_refresh，由 _schedule_refresh 安全上下文处理。
        """
        if width <= 0:
            return
        new_config = DisplayConfig(width)
        self.max_history = new_config.max_tool_history_items

        # 刷新渲染器宽度（无锁，直接写简单属性）
        if self._adapter is not None:
            self._adapter.force_refresh_width()

        # ★ 设置缩放刷新标记（信号安全：仅设置布尔值，无锁无 I/O）
        self._needs_resize_refresh = True

    # ── 耗时定时刷新 ────────────────────────────────────

    async def _elapsed_ticker(self) -> None:
        """后台任务：定期刷新面板以更新耗时显示（回答/思考耗时实时更新）。

        当 Agent 处于 thinking/answering 等状态但无状态变更（无新 token/工具调用）时，
        _schedule_refresh 不会触发（版本号未变），导致耗时数字停滞。
        此任务以 ~2Hz 频率强制刷新帧，确保耗时显示持续更新。
        """
        try:
            while not self._stopped:
                await asyncio.sleep(_ELAPSED_TICKER_INTERVAL)
                if not self._stopped and self._adapter is not None:
                    try:
                        self._render_frame(force=True)
                    except Exception:
                        _logger.exception(
                            "elapsed_ticker 渲染异常，跳过本次刷新"
                        )
        except asyncio.CancelledError:
            pass  # 正常取消，优雅退出

    # ── diff_active 上下文 ──────────────────────────────

    def _diff_active_guard(self, capture_frame: bool = True):
        """diff_active 上下文管理器 — 清除旧帧并阻止渲染。

        Returns:
            _DiffGuard 实例
        """
        return _DiffGuard(self, capture_frame)

    # ── 注册 ────────────────────────────────────────────

    def add_agent(self, label: str, description: str, status: str = "running",
                  agent_type: str = "ordinary"):
        self._store.add_agent(label, description, status, agent_type=agent_type)
        self._schedule_refresh()

    # ── 状态更新（代理到 AgentStateStore） ─────────────

    def update_agent_status(self, label: str, status: str):
        self._store.update_agent_status(label, status)
        self._schedule_refresh()

    def update_status(self, label: str, status: str):
        return self.update_agent_status(label, status)

    def update_model_phase(self, label: str, phase: str, info: str = ""):
        self._store.update_model_phase(label, phase, info)
        self._schedule_refresh()

    def tool_parsing(self, label: str, tool_name: str, arguments: str = ""):
        self._store.tool_parsing(label, tool_name, arguments)
        self._schedule_refresh()

    def tool_batch_start(self, label: str, tool_names: list):
        self._store.tool_batch_start(label, tool_names)
        self._schedule_refresh()

    def tool_start(self, label: str, tool_name: str, detail: str = "",
                   metadata: dict | None = None):
        self._store.tool_start(label, tool_name, detail)
        self._schedule_refresh()

    def tool_done(self, label: str, tool_name: str = "",
                  success: bool = True, metadata: dict | None = None):
        self._store.tool_done(label, tool_name, success)
        self._schedule_refresh()

    def update_parse_info(self, label: str, tool_names: str,
                          tokens: int, elapsed: float):
        self._store.update_parse_info(label, tool_names, tokens, elapsed)
        self._schedule_refresh()

    def parse_info_done(self, label: str) -> None:
        pass

    def update_tokens(self, label: str, tokens: int):
        self._store.update_tokens(label, tokens)

    def update_usage(self, label: str, usage: dict, replace: bool = False):
        self._store.update_usage(label, usage, replace)

    def update_live_output(self, label: str, tokens: int):
        self._store.update_live_output(label, tokens)
        # EventBus 发布去抖
        now = time.time()
        if now - self._last_eventbus_time >= _EVENTBUS_THROTTLE:
            self._last_eventbus_time = now
            try:
                DisplayEventBus.get_default().publish(LiveOutputEvent(
                    label=label, tokens=tokens, source=label,
                ))
            except Exception:
                _logger.debug("EventBus 发布 LiveOutputEvent 失败（非关键路径，忽略）")

    def update_live_input(self, label: str, tokens: int):
        self._store.update_live_input(label, tokens)

    def update_speed(self, label: str, speed: float):
        self._store.update_speed(label, speed)

    def set_result(self, label: str, result_text: str = "", error: str = ""):
        self._store.set_result(label, result_text, error)
        self._schedule_refresh()

    # ── 帧渲染（直接通过 OutputAdapter） ──────────────

    def _schedule_refresh(self) -> None:
        """节流调度帧刷新 — 仅在间隔足够且 adapter 就绪时渲染。

        安全上下文（非信号处理器）：在此处检查 _needs_resize_refresh 标记，
        刷新 _scroll_end 后调用 _render_frame(force=True) 强制重绘。
        在 output_lock 外调用（_render_frame 内部自行管理锁）。
        """
        if self._adapter is None:
            return

        # ★ 缩放刷新标记处理（由 _on_resize 信号安全上下文中设置）
        if self._needs_resize_refresh:
            self._needs_resize_refresh = False
            # 重新获取 scroll_end（缩放后底部行高可能变化）
            try:
                import src.chat_ui as _chat_ui_mod  # noqa: PLC0415
                _chat_ui = _chat_ui_mod.get_active_chat_ui()
                if _chat_ui is not None:
                    se = _chat_ui.bottom_bar.get_scroll_end()
                    self._scroll_end = int(se) if se is not None else 0
            except Exception:
                pass
            self._render_frame(force=True)
            return

        # 版本未变化时跳过（避免无意义消耗节流时隙）
        if self._store.version == self._last_rendered_version:
            return

        now = time.time()
        if now - self._last_refresh_time >= _REFRESH_INTERVAL:
            self._last_refresh_time = now
            self._render_frame()

    def _render_frame(self, force: bool = False, final: bool = False) -> None:
        """渲染当前帧到终端。

        直接通过 OutputAdapter 写入帧缓冲区，不经过 chat_ui 命令队列。
        OutputAdapter.write_raw() 内部自动管理 output_lock。

        Args:
            force: 跳过版本号检查强制渲染
            final: 最终帧
        """
        if self._adapter is None:
            return

        current_version = self._store.version
        if not final and not force and current_version == self._last_rendered_version:
            return
        self._last_rendered_version = current_version

        self._frame += 1
        self._renderer.sync_terminal_state(
            width=self._adapter.width,
            frame=self._frame,
        )
        lines = self._renderer.render(
            slots_snapshot=self._store.snapshot_all(),
            order=self._store.get_order(),
            now=time.time(),
            final=final,
        )

        self._write_frame_buffer(lines)

    def _write_frame_buffer(self, lines: list[str]) -> None:
        """构建帧缓冲区并通过 OutputAdapter 写入。

        使用绝对行号 \033[{row};1H 定位面板在 DECSTBM 滚动区域底部。
        相较于旧的 sc/rc 方式，不受内容区滚动影响。
        无 scroll_end（非 chat_ui 模式）时降级到旧 sc/rc 行为。
        """
        try:
            from .._blessed import get_terminal
            term = get_terminal()
            clear_eol = term.clear_eol if term.clear_eol else "\033[K"
        except Exception:
            clear_eol = "\033[K"

        total = len(lines)

        # ── 主路径：使用绝对行号定位（scroll_end 已知） ──
        if self._scroll_end > 0:
            buf = ""
            # 清除旧面板区域（防止面板缩小时残留）
            if self._last_lines > 0:
                old_start = self._scroll_end - self._last_lines + 1
                if old_start < 1:
                    old_start = 1
                for r in range(old_start, self._scroll_end + 1):
                    buf += f"\033[{r};1H{clear_eol}"

            # 新面板起始行（紧贴滚动区域底部）
            start_row = self._scroll_end - total + 1
            if start_row < 1:
                start_row = 1
            buf += f"\033[{start_row};1H"

            for i, line in enumerate(lines):
                buf += line
                if i < total - 1:
                    buf += "\n"

            self._adapter.write_raw(buf)
            self._last_lines = total
            return

        # ── 降级路径：无 scroll_end 时使用旧 sc/rc ──
        try:
            from .._blessed import get_terminal
            term = get_terminal()
            move_up = term.move_up
            sc = term.sc if term.sc else "\033[s"
            rc = term.rc if term.rc else "\033[u"
        except Exception:
            move_up = lambda n: f"\033[{n}A"
            sc = "\033[s"
            rc = "\033[u"

        buf = ""
        if self._last_lines > 0:
            buf += rc
            buf += move_up(self._last_lines)

        for i, line in enumerate(lines):
            buf += "\r" + clear_eol + line
            if i < total - 1:
                buf += "\n"

        extra = self._last_lines - total
        if extra > 0:
            buf += "\n" + sc
            for _ in range(extra):
                buf += "\n" + clear_eol
        else:
            buf += "\n" + sc
        self._adapter.write_raw(buf)
        self._last_lines = total

    def _clear_frame_lines(self) -> None:
        """清除终端上的帧行。

        有 scroll_end 时使用绝对行号清除，否则降级到旧 sc/rc 行为。
        """
        if self._adapter is None or self._last_lines <= 0:
            return

        # ── 主路径：绝对行号清除 ──
        if self._scroll_end > 0:
            try:
                from .._blessed import get_terminal
                term = get_terminal()
                clear_eol = term.clear_eol if term.clear_eol else "\033[K"
            except Exception:
                clear_eol = "\033[K"

            start = self._scroll_end - self._last_lines + 1
            if start < 1:
                start = 1
            code = ""
            for r in range(start, self._scroll_end + 1):
                code += f"\033[{r};1H{clear_eol}"
            self._adapter.write_raw(code)
            self._last_lines = 0
            return

        # ── 降级路径 ──
        try:
            from .._blessed import get_terminal
            term = get_terminal()
            clear_eol = term.clear_eol if term.clear_eol else "\033[K"
            move_up = term.move_up
            rc = term.rc if term.rc else "\033[u"
        except Exception:
            clear_eol = "\033[K"
            move_up = lambda n: f"\033[{n}A"
            rc = "\033[u"

        code = rc + move_up(self._last_lines)
        for _ in range(self._last_lines):
            code += "\r" + clear_eol + "\n"
        code += move_up(self._last_lines)
        self._adapter.write_raw(code)
        self._last_lines = 0

    # ── 生命周期 ────────────────────────────────────────

    def start(self):
        if self._started:
            return
        self._started = True
        self._stopped = False

        import src.chat_ui as _chat_ui_mod  # noqa: PLC0415
        _chat_ui = _chat_ui_mod.get_active_chat_ui()
        if _chat_ui is not None:
            self._adapter = _chat_ui.output_adapter
            # ★ 保存 DECSTBM 滚动区域底部行号，供 _write_frame_buffer 绝对定位使用
            try:
                se = _chat_ui.bottom_bar.get_scroll_end()
                self._scroll_end = int(se) if se is not None else 0
            except Exception:
                self._scroll_end = 0
            # 首次渲染
            from .._lock import _try_acquire_output_lock
            with _try_acquire_output_lock(
                name="parallel_display.start", timeout=0.5,
            ) as _locked:
                if _locked:
                    _chat_ui.ensure_cursor_upper()
                    self._render_frame()
                else:
                    self._render_frame()

        # 注册终端 resize 回调
        register_sigwinch_callback(self._on_resize)

        # 启动后台耗时定时刷新任务（~2Hz，确保 thinking/answering 耗时实时更新）
        try:
            loop = asyncio.get_running_loop()
            self._refresh_task = loop.create_task(self._elapsed_ticker())
        except RuntimeError:
            _logger.debug("启动 elapsed_ticker 失败（无运行中事件循环，非关键路径静默跳过）")

    def refresh(self, force: bool = False):
        """公开刷新入口 — 直接渲染当前帧到终端。

        Args:
            force: 是否跳过版本号检查强制渲染。
        """
        if self._adapter is not None:
            self._render_frame(force=force)

    # ── 停止 ────────────────────────────────────────────

    def stop(self, final: bool = False) -> None:
        """停止显示（实现 DisplayPort 接口）。

        清除终端上的并行面板。

        Args:
            final: 是否为最终停止
        """
        if self._finished:
            return
        self._finished = True
        self._stopped = True

        # 取消后台耗时定时刷新任务
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            self._refresh_task = None

        # 注销终端 resize 回调
        unregister_sigwinch_callback(self._on_resize)

        # 清除终端帧
        if self._adapter is not None:
            self._clear_frame_lines()
            self._adapter.flush()
        self._adapter = None

    async def await_stop(self, timeout: float = 2.0):
        """异步停止（兼容旧调用方，委托给 stop）。"""
        self.stop()

    # ── 特殊输出 ───────────────────────────────────────

    def capture_and_print(self, func) -> Any:
        """同步捕获 func 的自定义输出并写入终端。"""
        from io import StringIO
        import contextlib
        buf = StringIO()
        with contextlib.redirect_stdout(buf):
            result = func()
        diff_text = buf.getvalue()
        if diff_text:
            text = diff_text.rstrip()
            self._terminal.write_line(text)
        return result

    async def capture_and_print_async(self, async_func) -> Any:
        """异步版 capture_and_print。"""
        from io import StringIO
        import contextlib

        async def _run():
            buf = StringIO()
            async with self._capture_lock:
                with contextlib.redirect_stdout(buf):
                    result = await async_func()
            diff_text = buf.getvalue()
            if diff_text:
                text = diff_text.rstrip()
                self._terminal.write_line(text)
            return result

        with self._diff_active_guard(capture_frame=True):
            return await _run()

    def clear_frame_and_run(self, func) -> Any:
        """清除显示帧然后执行 func（func 直接写 stdout）。"""
        with self._diff_active_guard(capture_frame=True):
            return func()

    def print_output(self, text: str):
        """输出文本到终端，清除当前帧并替换。"""
        if not text:
            return
        self._clear_frame_lines()
        self._terminal.write_line(text)
