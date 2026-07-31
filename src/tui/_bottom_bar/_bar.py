"""底部栏主模块 — _BottomBar 终端底部固定输入栏。

从 ``_bottom_bar.py`` 提取为独立子模块，委托各子模块执行具体操作。
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from typing import TYPE_CHECKING, Any, Callable

from src.renderer._locks import _try_acquire_output_lock
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
)
from src.tui._bottom_bar._monitor import _SystemMonitor
from src.tui._bottom_bar._popup import _CompletionPopup
from src.tui._bottom_bar._state import BottomBarStatus
from src.tui._bottom_bar._layout import (
    _BOTTOM_MIN_HEIGHT,
    _BOTTOM_MIN_LINES,
    _MIN_INPUT_ROWS,
    _is_narrow,
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
from src.tui._bottom_bar._status import _format_duration

if TYPE_CHECKING:
    from src.tui._input import Input
    from src.tui._cursor_tracker import CursorTracker

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# _BottomBar — 终端底部固定输入栏
# ═══════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════
# 上帝类评估（方向④·步骤 10.2 原始；方向E·步骤9 已实施状态域收敛）：
# _BottomBar 状态收敛 — 状态域已收敛到 BottomBarStatus（_state.py，加锁+快照），
# 其余职责域（布局/动画/补全/监控）保留在 _BottomBar，以下按 6 个职责域
# 分组清单作为后续拆分依据（当前状态）：
#   1. 状态/生命周期：_active / _last_text / _last_status /
#      _needs_full_repaint（_status_active 已收敛到 _status 状态对象）
#   2. 状态域（已收敛）：_model_name / _tool_count / _tool_fail_count /
#      _tool_total / _main_phase / _main_phase_start / _tool_phase_start /
#      _status_active → BottomBarStatus（_state.py）；_subagent_lines /
#      _subagent_lines_lock / _last_subagent_lines 保留于 _bar（面板行，非状态域）
#   3. 布局/光标：_last_bottom_lines / _input_cursor_pos / _last_cursor_pos /
#      _cached_wrapped_for / _cached_wrapped_width / _cached_wrapped_lines /
#      _cached_input_rows / _last_rendered_text / _last_scroll_end /
#      _last_height / _last_sync_height
#   4. 动画：_animator
#   5. 补全：_completion
#   6. 监控：_system_monitor / _cached_cpu_percent / _cached_mem_percent /
#      _last_system_stats_time / _SYSTEM_STATS_INTERVAL
# 状态写入点已由公开访问器覆盖：set_active() / is_active（步骤 7.3）。
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
        # P3-14：_last_status / _last_subagent_lines 为只写不读死字段，已删除
        # 状态域（方向E·步骤9 收敛）：BottomBarStatus 状态对象（加锁 + 快照）
        self._status = BottomBarStatus()
        # subagent 面板行（非状态域，保留于 _bar；已有 _subagent_lines_lock）
        self._subagent_lines: list[str] = []
        self._subagent_lines_lock = threading.Lock()
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

    def set_active(self, active: bool) -> None:
        """设置底部栏激活状态（公开访问器，收敛私有字段写入）。"""
        self._active = active

    @property
    def is_completion_visible(self) -> bool:
        return self._completion.is_visible

    @property
    def is_status_active(self) -> bool:
        return self._status_active

    # ── 状态域属性委托（方向E·步骤9：BottomBarStatus 加锁+快照） ──
    # 读端经 snapshot() 一次性取快照（线程安全）；写端经 setter 委托
    # 状态对象受锁写入。保持私有属性读写路径向后兼容（测试/渲染读取）。

    @property
    def _status_active(self) -> bool:
        return self._status.snapshot()["status_active"]

    @_status_active.setter
    def _status_active(self, value: bool) -> None:
        self._status.update(status_active=bool(value))

    @property
    def _model_name(self) -> str:
        return self._status.snapshot()["model_name"]

    @_model_name.setter
    def _model_name(self, value: str) -> None:
        self._status.update(model_name=value)

    @property
    def _tool_count(self) -> int:
        return self._status.snapshot()["tool_count"]

    @_tool_count.setter
    def _tool_count(self, value: int) -> None:
        self._status.update(tool_count=value)

    @property
    def _tool_fail_count(self) -> int:
        return self._status.snapshot()["tool_fail_count"]

    @_tool_fail_count.setter
    def _tool_fail_count(self, value: int) -> None:
        self._status.update(tool_fail_count=value)

    @property
    def _tool_total(self) -> int:
        return self._status.snapshot()["tool_total"]

    @_tool_total.setter
    def _tool_total(self, value: int) -> None:
        self._status.update(tool_total=value)

    @property
    def _main_phase(self) -> str:
        return self._status.snapshot()["main_phase"]

    @_main_phase.setter
    def _main_phase(self, value: str) -> None:
        self._status.update(main_phase=value)

    @property
    def _main_phase_start(self) -> float:
        return self._status.snapshot()["main_phase_start"]

    @_main_phase_start.setter
    def _main_phase_start(self, value: float) -> None:
        self._status.update(main_phase_start=value)

    @property
    def _tool_phase_start(self) -> float:
        return self._status.snapshot()["tool_phase_start"]

    @_tool_phase_start.setter
    def _tool_phase_start(self, value: float) -> None:
        self._status.update(tool_phase_start=value)

    @property
    def _completion_idx(self) -> int:
        return self._completion._idx

    @_completion_idx.setter
    def _completion_idx(self, value: int) -> None:
        # P2-11：委托 _CompletionPopup 正式方法（消除对私有字段直改）
        self._completion.set_idx(value)

    @property
    def _completion_popup_height(self) -> int:
        return self._completion._popup_height

    @_completion_popup_height.setter
    def _completion_popup_height(self, value: int) -> None:
        # P2-11：委托 _CompletionPopup 正式方法（消除对私有字段直改）
        self._completion.set_popup_height(value)

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
            # P2-10：空异常捕获 → 记 debug 日志（exc_info 保留堆栈，便于诊断）
            _logger.debug("_update_system_stats: 获取 CPU/MEM 统计失败", exc_info=True)

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

    def set_tracker(self, tracker) -> None:
        """设置 stdout 行跟踪器（由装配层注入，与 RenderOutput 共享实例）。"""
        self._tracker = tracker

    def set_input_state(self, text: str, cursor_pos: int) -> None:
        self._last_text = text
        self._input_cursor_pos = cursor_pos

    def set_main_phase(self, phase: str) -> None:
        """设置主阶段（委托 BottomBarStatus；阶段变化时更新起始时间）。"""
        self._status.set_main_phase(phase)

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
        # 字段赋值委托 _CompletionPopup.show（方向E·步骤10 正式方法）
        self._completion.show(
            visible_items, selected_idx, popup_height,
            title=title, texts=texts, start_pos=start_pos,
            orig_prefix=orig_prefix, types=types, match_prefix=match_prefix,
        )
        self.force_redraw()

    def hide_completions(self) -> None:
        if not self._completion.is_visible or not self._active:
            return
        # 委托 _CompletionPopup.hide（内部保存 _last_idx_before_hide 后清空）
        self._completion.hide()
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
        # P3-14：_last_status 死字段已删除（原 self._last_status = status）
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

    # ── 状态管理（方向E·步骤9：委托 BottomBarStatus） ──

    def enable_status(self) -> None:
        self._status.enable_status()
        # P3-14：_last_status 死字段已删除（原 self._last_status = ""）

    def disable_status(self) -> None:
        self._status.disable_status()

    def increment_tool(self) -> None:
        self._status.increment_tool()

    def decrement_tool(self) -> None:
        self._status.decrement_tool()

    def increment_tool_fail(self) -> None:
        self._status.increment_tool_fail()

    def reset_tool_count(self) -> None:
        self._status.reset_tool_count()

    def set_model_name(self, name: str) -> None:
        self._status.set_model_name(name)

    def get_status_elapsed(self) -> float:
        """返回当前会话 token 速度快照的 elapsed_seconds（token 速度快照语义）。

        P3-13/P3-14 标注：本方法基于 ``_snapshot``（token 速度快照，由
        api.stats 维护），与 ``BottomBarStatus.get_status_elapsed_seconds``
        （状态对象方法，基于 _tool_phase_start/_main_phase_start 计算阶段/
        工具耗时）语义不同，勿混用。
        """
        try:
            from src.tui._snapshot import _get_snapshot
            snap_func = _get_snapshot()
            if snap_func is None:
                return 0.0
            return snap_func().get("elapsed_seconds", 0.0)
        except Exception:
            return 0.0

    def _format_status(self) -> str:
        # 状态文本构建单一入口收敛（2026-07-31 方向E）：
        # 阶段/工具耗时文本（「· 思考 3.20s」）唯一构建入口为
        # _status._build_status_text（_render 分隔线使用）；本方法
        # 仅组装模型名/工具计数/总耗时/token/速度段（基于 snapshot
        # 数据，与 _build_status_text 职责不同，不重复阶段文本逻辑）。
        st = self._status.snapshot()
        model_name = st["model_name"]
        status_active = st["status_active"]
        tool_count = st["tool_count"]
        tool_fail_count = st["tool_fail_count"]
        tool_total = st["tool_total"]
        if model_name:
            if status_active:
                _bf = self._animator.breath_frame
                if _bf > 0:
                    _pulse_color = self._animator.sine_color(36, 45, 4)
                else:
                    _pulse_color = 45
                model_part = (
                    f"\033[38;5;{_pulse_color}m\u00b7\033[0m"
                    f" {_COLOR_ACCENT}{model_name}{_COLOR_RESET}"
                )
            else:
                model_part = (
                    f"{_COLOR_ACCENT}\u00b7{_COLOR_RESET}"
                    f" {_COLOR_ACCENT}{model_name}{_COLOR_RESET}"
                )
        else:
            model_part = ""
        if not status_active:
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
        if total <= 0 and elapsed <= 0 and per_second_speed <= 0 and tool_total <= 0:
            return model_part
        parts = []
        if tool_total > 0:
            if not _is_narrow():
                glow_gear = f"{_build_glow_ansi(self._animator.frame, 45, 12)}\u00b7\033[0m "
            else:
                glow_gear = ""
            if tool_count > 0:
                if tool_fail_count > 0:
                    total_colored = f"{_COLOR_TOOL_FAIL}{tool_total}{_COLOR_RESET}"
                else:
                    total_colored = f"{_COLOR_TOOL_OK}{tool_total}{_COLOR_RESET}"
                parts.append(
                    f"{glow_gear}"
                    f"{_COLOR_ACCENT}{tool_count}{_COLOR_RESET}"
                    f"{_COLOR_DIM}\u2192{_COLOR_RESET}"
                    f"{total_colored}"
                )
            else:
                done = tool_total - tool_count - tool_fail_count
                if tool_fail_count > 0:
                    parts.append(
                        f"{glow_gear}"
                        f"{_COLOR_TOOL_OK}{done}{_COLOR_RESET}"
                        f"{_COLOR_DIM}/{_COLOR_RESET}"
                        f"{_COLOR_TOOL_FAIL}{tool_total}{_COLOR_RESET}"
                    )
                else:
                    parts.append(f"{glow_gear}{_COLOR_TOOL_OK}{tool_total}{_COLOR_RESET}")
        if elapsed > 0:
            # P3-15：耗时格式化共享 _status._format_duration（默认 precision=1，
            # 保持原 .1f 语义；≥60s 用 mins:secs / hours:min:sec）
            dur = _format_duration(elapsed)
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
