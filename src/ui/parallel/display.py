"""
并行 Agent 显示 — Claude Code 风格（ChatUI 驱动版）

职责分层：
  - ParallelDisplay：生命周期控制 + 状态代理 + 刷新调度
  - FrameRenderer：纯函数渲染（state → 行列表）

渲染路径：ParallelDisplay → push_cmd(RenderCommand.SUBAGENT_FRAME) → 命令队列
  → render 线程出队 → ContentRenderer._do_subagent_frame() → 终端输出。

2026-06-12 重构：
  - 面板帧改为通过 RenderCommand.SUBAGENT_FRAME 命令队列渲染
  - 10Hz fps 状态更新（_phase_pre_update_panels）移到批量出队前执行
  - 移除直接调用 _write_frame_buffer() 的路径
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
)
from ...chat_ui._const import RenderCommand

# ── 常量 ────────────────────────────────────────────────

_EVENTBUS_THROTTLE = 0.3   # 300ms — EventBus 发布频率阈值，防止高频 update 路径过度发布
_REFRESH_INTERVAL = 0.1  # 100ms — 帧刷新节流间隔（10Hz，与 ChatUI render 线程一致）
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
    """并行 Agent 实时显示管理器 — 命令队列渲染版（代理层）

    职责：
    1. 生命周期控制（start/stop）
    2. 状态更新代理（代理到 AgentStateStore）
    3. 面板刷新调度（通过 RenderCommand.SUBAGENT_FRAME 命令队列渲染）
    4. 特殊输出（capture_and_print/print_output）

    帧渲染通过 RenderCommand 推送到 chat_ui 命令队列，
    由 render 线程的 ContentRenderer._do_subagent_frame() 消费并输出。
    fps 状态更新在 _drain_queue() 的 Phase 0（批量出队前）执行。
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

        # ★ push_cmd 回调（由 start() 从 ChatUIConsumer 获取，线程安全）
        self._push_cmd: Any = None

        # 帧状态
        self._frame: int = 0
        self._last_lines: int = 0
        self._last_rendered_version: int = 0
        # DECSTBM 滚动区域底部行号（由 start() 从 chat_ui bottom_bar 获取）
        self._scroll_end: int = 0
        # 缩放刷新标记（信号安全：在 _on_resize 中设置，_schedule_refresh 中消费）
        self._needs_resize_refresh: bool = False

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

    # ── 面板刷新回调（由 render 线程 10Hz Phase 0 调用） ──

    def _panel_refresh_callback(self) -> None:
        """面板刷新回调 — 由 chat_ui render 线程 _phase_pre_update_panels() 10Hz 调用。

        仅在存在 running 状态的 Agent 时工作：
        1. 更新 scroll_end（终端 resize 自适应）
        2. 推送 SUBAGENT_FRAME 命令到渲染队列（由 Phase 1 批量出队消费）

        注意：不在本回调中直接写终端，所有面板渲染都通过命令队列执行。
        """
        if self._adapter is None or self._stopped:
            return
        if self._store.has_running_agents:
            # 每帧刷新 scroll_end，确保终端 resize 后面板定位正确
            try:
                import src.chat_ui as _chat_ui_mod  # noqa: PLC0415
                _chat_ui = _chat_ui_mod.get_active_chat_ui()
                if _chat_ui is not None:
                    se = _chat_ui.bottom_bar.get_scroll_end()
                    if se is not None and se > 0:
                        new_se = int(se)
                        if new_se != self._scroll_end:
                            self._last_lines = 0
                            self._scroll_end = new_se
            except Exception:
                pass
            # 推送 SUBAGENT_FRAME 命令到渲染队列
            self._push_frame_cmd()

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

    # ── 帧渲染（通过命令队列） ────────────────────────

    def _schedule_refresh(self) -> None:
        """节流调度帧刷新 — 推送 SUBAGENT_FRAME 命令到渲染队列。

        安全上下文（非信号处理器）：在此处检查 _needs_resize_refresh 标记，
        刷新 _scroll_end 后推送命令。不在此处直接写终端。
        """
        if self._adapter is None:
            return

        # ★ 缩放刷新标记处理（由 _on_resize 信号安全上下文中设置）
        if self._needs_resize_refresh:
            self._needs_resize_refresh = False
            try:
                import src.chat_ui as _chat_ui_mod  # noqa: PLC0415
                _chat_ui = _chat_ui_mod.get_active_chat_ui()
                if _chat_ui is not None:
                    se = _chat_ui.bottom_bar.get_scroll_end()
                    self._scroll_end = int(se) if se is not None else 0
            except Exception:
                pass
            self._last_lines = 0
            self._push_frame_cmd()
            return

        # 版本未变化时跳过
        if self._store.version == self._last_rendered_version:
            return

        now = time.time()
        if now - self._last_refresh_time >= _REFRESH_INTERVAL:
            self._last_refresh_time = now
            self._push_frame_cmd()

    def _build_frame(self, final: bool = False) -> tuple | None:
        """构建面板帧数据（纯函数，不写终端）。

        渲染当前状态到行列表，打包为 (lines, scroll_end, last_lines, clear_eol) 元组，
        供 _push_frame_cmd() 推送到命令队列。

        Args:
            final: 是否结束帧

        Returns:
            (lines, scroll_end, last_lines, clear_eol) 或 None（adapter 缺失时）
        """
        if self._adapter is None:
            return None

        current_version = self._store.version
        if not final and current_version == self._last_rendered_version:
            return None
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

        try:
            from .._blessed import get_terminal
            term = get_terminal()
            clear_eol = term.clear_eol if term.clear_eol else "\033[K"
        except Exception:
            clear_eol = "\033[K"

        return (lines, self._scroll_end, self._last_lines, clear_eol)

    def _push_frame_cmd(self) -> None:
        """渲染当前帧并推送 SUBAGENT_FRAME 命令到 chat_ui 渲染队列。

        由 _schedule_refresh() 和 _panel_refresh_callback() 调用。
        帧数据在消费侧（ContentRenderer._do_subagent_frame）写入终端。
        """
        packed = self._build_frame()
        if packed is None:
            return
        # 更新 _last_lines 供下次 SU/SD delta 计算
        lines = packed[0]
        self._last_lines = len(lines)
        if self._push_cmd is not None:
            self._push_cmd((RenderCommand.SUBAGENT_FRAME, packed))

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
            # ★ 获取 push_cmd 回调（向命令队列推送 SUBAGENT_FRAME 命令）
            self._push_cmd = _chat_ui.push_cmd
            # ★ 保存 DECSTBM 滚动区域底部行号，供帧定位使用
            try:
                se = _chat_ui.bottom_bar.get_scroll_end()
                self._scroll_end = int(se) if se is not None else 0
            except Exception:
                self._scroll_end = 0
            # 首次渲染（推送 SUBAGENT_FRAME 命令到队列）
            from .._lock import _try_acquire_output_lock
            with _try_acquire_output_lock(
                name="parallel_display.start", timeout=0.5,
            ) as _locked:
                if _locked:
                    _chat_ui.ensure_cursor_upper()
                self._push_frame_cmd()

        # 注册终端 resize 回调
        register_sigwinch_callback(self._on_resize)

        # ★ 注册面板刷新回调到 chat_ui render 线程（10Hz），
        #   替代独立的 500ms 定时器，使 subagent 面板刷新与 render 线程同步。
        try:
            _chat_ui.set_panel_refresh_callback(self._panel_refresh_callback)
        except Exception:
            _logger.debug(
                "注册 panel_refresh_callback 失败（非关键路径，静默跳过）",
            )

    def refresh(self, force: bool = False):
        """公开刷新入口 — 推送 SUBAGENT_FRAME 命令到渲染队列。

        Args:
            force: 是否跳过版本号检查强制渲染（当前忽略，由 _build_frame 内部检查）。
        """
        if self._adapter is not None:
            self._push_frame_cmd()

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

        # ★ 注销面板刷新回调（render 线程不再调用）
        try:
            import src.chat_ui as _chat_ui_mod  # noqa: PLC0415
            _chat_ui = _chat_ui_mod.get_active_chat_ui()
            if _chat_ui is not None:
                _chat_ui.set_panel_refresh_callback(None)
        except Exception:
            _logger.debug("注销 panel_refresh_callback 失败", exc_info=True)

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
