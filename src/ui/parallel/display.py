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

        # stdout 捕获锁
        self._capture_lock = asyncio.Lock()

    # ── 终端缩放回调 ────────────────────────────────────

    def _on_resize(self, width: int, height: int) -> None:
        """终端缩放回调：重建 DisplayConfig + 刷新宽度。"""
        if width <= 0:
            return
        new_config = DisplayConfig(width)
        self.max_history = new_config.max_tool_history_items

        # 刷新渲染器宽度
        if self._adapter is not None:
            self._adapter.force_refresh_width()

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

    # ── 状态更新（代理到 AgentStateStore） ─────────────

    def update_agent_status(self, label: str, status: str):
        self._store.update_agent_status(label, status)

    def update_status(self, label: str, status: str):
        return self.update_agent_status(label, status)

    def update_model_phase(self, label: str, phase: str, info: str = ""):
        self._store.update_model_phase(label, phase, info)

    def tool_parsing(self, label: str, tool_name: str, arguments: str = ""):
        self._store.tool_parsing(label, tool_name, arguments)

    def tool_batch_start(self, label: str, tool_names: list):
        self._store.tool_batch_start(label, tool_names)

    def tool_start(self, label: str, tool_name: str, detail: str = "",
                   metadata: dict | None = None):
        self._store.tool_start(label, tool_name, detail)

    def tool_done(self, label: str, tool_name: str = "",
                  success: bool = True, metadata: dict | None = None):
        self._store.tool_done(label, tool_name, success)

    def update_parse_info(self, label: str, tool_names: str,
                          tokens: int, elapsed: float):
        self._store.update_parse_info(label, tool_names, tokens, elapsed)

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

    # ── 帧渲染（直接通过 OutputAdapter） ──────────────

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

        使用 ANSI 光标控制实现帧覆写（sc/rc 光标保存/恢复）。
        """
        try:
            from .._blessed import get_terminal
            term = get_terminal()
            move_up = term.move_up
            move_down = term.move_down
            clear_eol = term.clear_eol
            sc = term.sc if term.sc else "\033[s"
            rc = term.rc if term.rc else "\033[u"
        except Exception:
            move_up = lambda n: f"\033[{n}A"
            move_down = lambda n: f"\033[{n}B"
            clear_eol = "\033[K"
            sc = "\033[s"
            rc = "\033[u"

        total = len(lines)
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
        """清除终端上的帧行。"""
        if self._adapter is None or self._last_lines <= 0:
            return
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
