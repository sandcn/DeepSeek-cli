"""底部栏主模块 — _BottomBar 终端底部固定输入栏。

从 ``_bottom_bar.py`` 提取为独立子模块，委托各子模块执行具体操作。
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from typing import TYPE_CHECKING, Any, Callable

from src.tui._locks import _try_acquire_output_lock
from src.tui._screen import (
    _COLOR_ACCENT,
    _COLOR_DIM,
    _COLOR_RESET,
    _COLOR_SPEED,
    _COLOR_TIME,
    _COLOR_TOKEN,
    _COLOR_TOOL_FAIL,
    _COLOR_TOOL_OK,
    _get_terminal_size,
    cursor_goto,
    cursor_restore,
    cursor_save,
    TerminalWidthCache,
)
from src.tui._animator import AnimatorContext
from src.tui._input import (
    _compute_cursor_visual_pos,
    _expand_tabs,
    _wrap_by_width,
)
from src.tui._bottom_bar._monitor import _SystemMonitor
from src.tui._bottom_bar._popup import _CompletionPopup
from src.tui._bottom_bar._layout import (
    _BOTTOM_MIN_HEIGHT,
    _BOTTOM_MIN_LINES,
    _MIN_INPUT_ROWS,
    _is_narrow,
    _visual_width,
    _compute_input_rows,
    _compute_bottom_lines_for,
    _draw_input_lines,
    _build_glow_ansi,
)
from src.tui._bottom_bar._render import (
    _do_sync_bottom_lines,
    _do_ensure_cursor_in_upper,
    _do_ensure_cursor_in_lower,
    _do_register_sigwinch,
    _do_setup,
    _do_teardown,
    _do_force_redraw,
)
from src.tui._bottom_bar._status import _build_status_text

if TYPE_CHECKING:
    from src.tui._input import Input
    from src.tui._cursor_tracker import CursorTracker

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# _BottomBar — 终端底部固定输入栏
# ═══════════════════════════════════════════════════════════

class _BottomBar:
    """终端底部固定输入栏，流式输出期间始终可见。

    使用 ANSI DECSTBM 滚动区域：上方内容区正常滚动，
    底部行位于滚动区域之外，通过手动定位绘制保持固定。
    """

    _MIN_HEIGHT = _BOTTOM_MIN_HEIGHT

    def __init__(self, cursor_tracker: "CursorTracker | None" = None,
                 animator: AnimatorContext | None = None,
                 width_cache: TerminalWidthCache | None = None):
        self._active = False
        self._last_text = ""
        self._last_status = ""
        # _StatusMixin 字段
        self._status_active: bool = False
        self._model_name: str = ""
        self._tool_count: int = 0
        self._tool_fail_count: int = 0
        self._tool_total: int = 0
        self._subagent_lines: list[str] = []
        self._subagent_lines_lock = threading.Lock()
        self._last_subagent_lines: list[str] = []
        self._main_phase: str = ""
        self._main_phase_start: float = 0.0
        self._tool_phase_start: float = 0.0
        # 布局/光标
        self._last_bottom_lines = _BOTTOM_MIN_LINES
        self._input_cursor_pos: int = -1
        self._last_cursor_pos: int = -1
        self._cached_wrapped_for: str = ""
        self._cached_wrapped_width: int = 0
        self._cached_wrapped_lines: list[str] | None = None
        self._cached_input_rows: int = _MIN_INPUT_ROWS
        self._last_rendered_text: str = ""
        self._last_scroll_end: int = 0
        self._last_height: int = 0
        self._last_sync_height: int = 0
        # 动画时钟
        self._animator = animator or AnimatorContext.get_default()
        # 终端尺寸缓存
        self._width_cache = width_cache or TerminalWidthCache.get_default()
        # 补全弹窗
        self._completion = _CompletionPopup(cursor_tracker=cursor_tracker, animator=self._animator)
        # stdout 行追踪器
        from src.tui._stdout_tracker import _StdoutLineTracker
        self._tracker: _StdoutLineTracker | None = None
        # 光标坐标追踪器
        from src.tui._cursor_tracker import CursorTracker as _CT
        self._cursor_tracker = cursor_tracker or _CT()
        self._sigwinch_cb: Any = None
        self._needs_full_repaint: bool = False
        self._request_redraw_cb: Callable[[], None] | None = None
        self._input: "Input | None" = None
        # 系统监控
        self._system_monitor: _SystemMonitor | None = None
        self._cached_cpu_percent: float = 0.0
        self._cached_mem_percent: float = 0.0
        self._last_system_stats_time: float = 0.0
        self._SYSTEM_STATS_INTERVAL: float = 1.0

    # ── 属性 ──────────────────────────────────────

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def is_completion_visible(self) -> bool:
        return self._completion.is_visible

    @property
    def is_status_active(self) -> bool:
        return self._status_active

    @property
    def _completion_idx(self) -> int:
        return self._completion._idx

    @_completion_idx.setter
    def _completion_idx(self, value: int) -> None:
        self._completion._idx = value

    @property
    def _completion_popup_height(self) -> int:
        return self._completion._popup_height

    @_completion_popup_height.setter
    def _completion_popup_height(self, value: int) -> None:
        self._completion._popup_height = value

    @property
    def _bottom_lines(self) -> int:
        return 2 + len(self._subagent_lines) + self._compute_input_rows()

    # ── 尺寸查询 ──────────────────────────────────

    def _term_height(self) -> int:
        _, h = _get_terminal_size()
        return h or 24

    def _term_width(self) -> int:
        w, _ = _get_terminal_size()
        return w or 80

    def _compute_input_rows(self) -> int:
        return _compute_input_rows(self._last_text or "", self._term_width(), self._completion.height)

    def _compute_bottom_lines_for(self, text: str, term_width: int) -> int:
        return _compute_bottom_lines_for(text, term_width, len(self._subagent_lines), self._completion.height)

    # ── 系统监控 ──────────────────────────────────

    def _update_system_stats(self) -> None:
        now = time.monotonic()
        if now - self._last_system_stats_time < self._SYSTEM_STATS_INTERVAL:
            return
        self._last_system_stats_time = now
        if self._system_monitor is None:
            self._system_monitor = _SystemMonitor()
        try:
            cpu_pct, mem_pct = self._system_monitor.get_cpu_and_mem()
            self._cached_cpu_percent = cpu_pct
            self._cached_mem_percent = mem_pct
        except Exception:
            pass

    # ── resize 保护 ───────────────────────────────

    def set_full_repaint_needed(self) -> None:
        self._needs_full_repaint = True

    def force_refresh_dimensions(self) -> None:
        self._width_cache.force_refresh()
        self.set_full_repaint_needed()

    def set_request_redraw_cb(self, cb: Callable[[], None] | None) -> None:
        """设置请求重绘回调（由 TuiEngine.request_bottom_redraw 驱动）。"""
        self._request_redraw_cb = cb

    # ── 光标定位 ──────────────────────────────────

    def get_scroll_end(self) -> int:
        return self._last_scroll_end

    def get_cursor_info(self) -> tuple[str, int, int, int]:
        text = self._last_rendered_text if self._last_rendered_text else self._last_text
        cursor_pos = min(self._input_cursor_pos, len(text))
        return (text, cursor_pos, self._term_height(), self._term_width())

    def compute_cursor_position(self, text: str, cursor_pos: int, h: int, w: int) -> tuple[int, int]:
        if self._input is not None:
            bottom_for_text = self._compute_bottom_lines_for(text, w)
            r_cursor, cursor_col, _, _ = self._input.compute_cursor(
                text, cursor_pos, bottom_for_text,
                len(self._subagent_lines), self._completion.height,
            )
            return (r_cursor, cursor_col)
        max_input = max(1, w - 4)
        vis_row, vis_col = _compute_cursor_visual_pos(text, cursor_pos, max_input)
        total_bottom = max(5, self._compute_bottom_lines_for(text, w))
        popup_offset = self._completion.height
        subagent_offset = len(self._subagent_lines)
        r_cursor = max(1, h - total_bottom + 4 + subagent_offset + popup_offset + vis_row)
        cursor_col = min(3 + vis_col, w)
        return (r_cursor, cursor_col)

    # ── 同步滚动区域（委托 _render） ──────────────

    def sync_bottom_lines(self) -> None:
        _do_sync_bottom_lines(self)

    # ── 光标区域切换（委托 _render） ──────────────

    def ensure_cursor_in_upper(self) -> None:
        _do_ensure_cursor_in_upper(self)

    def ensure_cursor_in_lower(self) -> None:
        _do_ensure_cursor_in_lower(self)

    # ── 子Agent面板 ───────────────────────────────

    def set_subagent_frame(self, lines: list[str]) -> None:
        with self._subagent_lines_lock:
            self._subagent_lines = list(lines)

    # ── 生命周期（委托 _render） ──────────────────

    def set_input(self, input_instance: "Input") -> None:
        self._input = input_instance

    def set_input_state(self, text: str, cursor_pos: int) -> None:
        self._last_text = text
        self._input_cursor_pos = cursor_pos

    def set_main_phase(self, phase: str) -> None:
        if phase != self._main_phase:
            self._main_phase_start = time.monotonic()
        self._main_phase = phase

    def _register_sigwinch(self) -> None:
        _do_register_sigwinch(self)

    def setup(self) -> None:
        _do_setup(self)

    def teardown(self) -> None:
        _do_teardown(self)

    # ── force_redraw（委托 _render） ──────────────

    def force_redraw(self) -> None:
        _do_force_redraw(self)

    # ── 输入行绘制（委托 _layout） ────────────────

    def _draw_input_lines(self, out, text: str, r_start: int, term_width: int) -> None:
        _draw_input_lines(self, out, text, r_start, term_width)

    # ── 补全弹窗委托 ──────────────────────────────

    def show_completions(self, items: list[str], selected_idx: int,
                         texts: list[str] | None = None, start_pos: int = 0,
                         orig_prefix: str = "", title: str = "补全",
                         types: list[str] | None = None,
                         match_prefix: str = "") -> None:
        if not items or not self._active:
            return
        total_items = len(items)
        h_items = min(total_items, _CompletionPopup._COMPLETION_MAX_ITEMS)
        popup_height = h_items + 2
        max_avail = self._term_height() - 7
        if max_avail <= 0:
            return
        if popup_height > max_avail:
            h_items = max(1, max_avail - 2)
            popup_height = h_items + 2
        visible_items = items[:h_items]
        selected_idx = min(selected_idx, h_items - 1)
        self._completion._popup_height = popup_height
        self._completion._visible = True
        self._completion._title = title
        self._completion._is_selection = (title != "补全")
        self._completion._items = list(visible_items)
        self._completion._texts = list(texts) if texts is not None else list(visible_items)
        self._completion._idx = selected_idx
        self._completion._start_pos = start_pos
        self._completion._orig_prefix = orig_prefix
        self._completion._types = list(types) if types is not None else []
        self._completion._match_prefix = match_prefix
        self.force_redraw()

    def hide_completions(self) -> None:
        if not self._completion.is_visible or not self._active:
            return
        saved_idx = self._completion._idx
        self._completion._last_idx_before_hide = saved_idx
        self._completion._popup_height = 0
        self._completion._visible = False
        self._completion._title = "补全"
        self._completion._is_selection = False
        self._completion._items = []
        self._completion._texts = []
        self._completion._idx = 0
        self._completion._start_pos = 0
        self._completion._orig_prefix = ""
        self._completion._types = []
        self._completion._match_prefix = ""
        self.force_redraw()

    def cycle_completion(self, delta: int = 1) -> int:
        if not self._completion.is_visible or not self._completion._items:
            return 0
        self._completion.cycle(delta)
        with _try_acquire_output_lock(name="bottom_bar.cycle_completion", timeout=0.3) as locked:
            if locked:
                self._redraw_cycle_only()
        return self._completion._idx

    def get_selected_completion(self) -> tuple[str, int, str]:
        return self._completion.get_selected()

    def get_selected_completion_index(self) -> int:
        if self._completion.is_visible:
            return self._completion._idx
        return self._completion._last_idx_before_hide

    def _redraw_cycle_only(self) -> None:
        if not self._completion.is_visible or not self._completion._items:
            return
        out = sys.__stdout__
        out.write(cursor_save())
        height = self._term_height()
        total = self._bottom_lines
        popup_start = height - total + 3 + len(self._subagent_lines)
        tw = self._term_width()
        self._completion.render_cycle_update(out, popup_start, tw)
        r2 = height - total + 2 + len(self._subagent_lines)
        status = self._format_status()
        self._last_status = status
        if status:
            if self._animator.breath_frame > 0 and not _is_narrow():
                dot_color = self._animator.sine_color(45, 81, 12)
                dot_ansi = f"\033[38;5;{dot_color}m\u00b7{_COLOR_RESET}"
                out.write(f"{cursor_goto(r2, 1)}\033[K" + status + " " + dot_ansi)
            else:
                out.write(f"{cursor_goto(r2, 1)}\033[K" + status)
        else:
            out.write(f"{cursor_goto(r2, 1)}\033[K")
        out.write(cursor_restore())
        out.flush()
        self._last_height = height

    # ── 状态管理（_StatusMixin 内联） ───────────────

    def enable_status(self) -> None:
        self._status_active = True
        self._last_status = ""

    def disable_status(self) -> None:
        self._status_active = False

    def increment_tool(self) -> None:
        if self._tool_count == 0:
            self._tool_phase_start = time.monotonic()
        self._tool_count += 1
        self._tool_total += 1

    def decrement_tool(self) -> None:
        self._tool_count = max(0, self._tool_count - 1)

    def increment_tool_fail(self) -> None:
        self._tool_fail_count += 1

    def reset_tool_count(self) -> None:
        self._tool_count = 0
        self._tool_fail_count = 0
        self._tool_total = 0
        self._tool_phase_start = 0.0

    def set_model_name(self, name: str) -> None:
        self._model_name = name

    def get_status_elapsed(self) -> float:
        try:
            from src.tui._snapshot import _get_snapshot
            snap_func = _get_snapshot()
            if snap_func is None:
                return 0.0
            return snap_func().get("elapsed_seconds", 0.0)
        except Exception:
            return 0.0

    def _format_status(self) -> str:
        if self._model_name:
            if self._status_active:
                _bf = self._animator.breath_frame
                if _bf > 0:
                    _pulse_color = self._animator.sine_color(36, 45, 4)
                else:
                    _pulse_color = 45
                model_part = (
                    f"\033[38;5;{_pulse_color}m\u00b7\033[0m"
                    f" {_COLOR_ACCENT}{self._model_name}{_COLOR_RESET}"
                )
            else:
                model_part = (
                    f"{_COLOR_ACCENT}\u00b7{_COLOR_RESET}"
                    f" {_COLOR_ACCENT}{self._model_name}{_COLOR_RESET}"
                )
        else:
            model_part = ""
        if not self._status_active:
            return model_part
        try:
            from src.tui._snapshot import _get_snapshot
            snap_func = _get_snapshot()
            if snap_func is None:
                return model_part
            snap = snap_func()
        except Exception:
            return model_part
        total = snap.get("total_tokens", 0)
        elapsed = snap.get("elapsed_seconds", 0.0)
        per_second_speed = snap.get("per_second_speed", 0.0)
        if total <= 0 and elapsed <= 0 and per_second_speed <= 0 and self._tool_total <= 0:
            return model_part
        parts = []
        if self._tool_total > 0:
            if not _is_narrow():
                glow_gear = f"{_build_glow_ansi(self._animator.frame, 45, 12)}\u00b7\033[0m "
            else:
                glow_gear = ""
            if self._tool_count > 0:
                if self._tool_fail_count > 0:
                    total_colored = f"{_COLOR_TOOL_FAIL}{self._tool_total}{_COLOR_RESET}"
                else:
                    total_colored = f"{_COLOR_TOOL_OK}{self._tool_total}{_COLOR_RESET}"
                parts.append(
                    f"{glow_gear}"
                    f"{_COLOR_ACCENT}{self._tool_count}{_COLOR_RESET}"
                    f"{_COLOR_DIM}\u2192{_COLOR_RESET}"
                    f"{total_colored}"
                )
            else:
                done = self._tool_total - self._tool_count - self._tool_fail_count
                if self._tool_fail_count > 0:
                    parts.append(
                        f"{glow_gear}"
                        f"{_COLOR_TOOL_OK}{done}{_COLOR_RESET}"
                        f"{_COLOR_DIM}/{_COLOR_RESET}"
                        f"{_COLOR_TOOL_FAIL}{self._tool_total}{_COLOR_RESET}"
                    )
                else:
                    parts.append(f"{glow_gear}{_COLOR_TOOL_OK}{self._tool_total}{_COLOR_RESET}")
        if elapsed > 0:
            if elapsed >= 60:
                mins = int(elapsed // 60)
                secs = int(elapsed % 60)
                dur = f"{mins}:{secs:02d}" if mins < 60 else f"{mins // 60}:{mins % 60:02d}:{secs:02d}"
            else:
                dur = f"{elapsed:.1f}s"
            parts.append(f"{_COLOR_TIME}{dur}{_COLOR_RESET}")
        if total > 0:
            tok_str = f"{total / 1000:.1f}k" if total >= 1000 else str(total)
            parts.append(f"{_COLOR_TOKEN}{tok_str}t{_COLOR_RESET}")
        if per_second_speed > 0:
            speed_str = f"{per_second_speed:.1f}" if per_second_speed >= 1 else f"{per_second_speed:.2f}"
            parts.append(f"{_COLOR_SPEED}{speed_str}t/s{_COLOR_RESET}")
        sep = f" {_COLOR_DIM}\u00b7{_COLOR_RESET} "
        status = sep.join(parts) if parts else ""
        if status and not _is_narrow():
            glow_dot = f"{_build_glow_ansi(self._animator.frame, 45, 12)}\u00b7\033[0m"
            status = f"{status}  {glow_dot}"
        if model_part and status:
            return f"{model_part}  {status}"
        return model_part or status


__all__ = ["_BottomBar"]
