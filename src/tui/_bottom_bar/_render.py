"""底部栏渲染引擎模块 — DECSTBM 滚动区域管理 + force_redraw + 生命周期。

从 ``_bottom_bar.py`` 提取为独立子模块。

包含：
  - _build_gradient — 渐变分隔线构建
  - 滚动区域同步（sync_bottom_lines）
  - 光标区域切换（ensure_cursor_in_upper / ensure_cursor_in_lower）
  - 生命周期（setup / teardown / _register_sigwinch）
  - force_redraw（主重绘方法）
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

from src.renderer._locks import _try_acquire_output_lock
from src.tui._screen import (
    _COLOR_ACCENT,
    _COLOR_RESET,
    _COLOR_SEP,
    cursor_goto,
    cursor_restore,
    cursor_save,
    register_sigwinch_callback,
    reset_scroll_region,
    scroll_up,
    set_scroll_region,
    sgr_reset,
    unregister_sigwinch_callback,
)
from src.tui._stdout_tracker import _StdoutLineTracker as _ST
from src.tui._animator import AnimatorContext
from src.tui._input import _compute_cursor_visual_pos
from src.tui._bottom_bar._layout import (
    _BOTTOM_MIN_HEIGHT,
    _BOTTOM_MIN_LINES,
    _is_narrow,
    _ansi_truncate,
    _visual_width,
    _compute_input_rows,
    _draw_input_lines,
)
from src.tui._bottom_bar._status import _build_status_text

if TYPE_CHECKING:
    from src.tui._bottom_bar._bar import _BottomBar

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# 渐变分隔线（提取自 _bottom_bar_old.py）
# ═══════════════════════════════════════════════════════════

def _build_gradient(width: int, start_color: int = 45, end_color: int = 237,
                    char: str = "\u2501") -> str:
    """构建渐变分隔线。

    Args:
        width: 分隔线宽度（列数）。
        start_color: 起始 256 色号。
        end_color: 结束 256 色号。
        char: 分隔线字符。

    Returns:
        带 ANSI 颜色序列的分隔线字符串。
    """
    if width <= 0:
        return ""
    parts: list[str] = []
    for i in range(width):
        ratio = i / max(1, width - 1)
        color = start_color + int((end_color - start_color) * ratio)
        parts.append(f"\033[38;5;{color}m{char}")
    parts.append("\033[0m")
    return "".join(parts)


# ═══════════════════════════════════════════════════════════
# 滚动区域同步（提取自 _BottomBar.sync_bottom_lines）
# ═══════════════════════════════════════════════════════════

def _do_sync_bottom_lines(bb: "_BottomBar") -> None:
    """同步 DECSTBM 滚动区域到当前底部行。

    Args:
        bb: _BottomBar 实例。
    """
    if not bb._active:
        return
    height = bb._term_height()
    scroll_end = height - bb._bottom_lines
    if scroll_end == bb._last_scroll_end and height == bb._last_sync_height:
        return
    resized = height != bb._last_sync_height
    if scroll_end < 1:
        scroll_end = height
    old_scroll = bb._last_scroll_end
    bb._last_scroll_end = scroll_end
    bb._last_sync_height = height
    if bb._tracker is not None:
        bb._tracker.set_scroll_end(scroll_end)
    out = sys.__stdout__
    _buf = [set_scroll_region(1, scroll_end)]
    if not resized:
        if scroll_end >= 1:
            _buf.append(f"{cursor_goto(scroll_end, 1)}\033[K")
            if old_scroll > scroll_end:
                for r in range(scroll_end + 1, min(old_scroll, height) + 1):
                    _buf.append(f"{cursor_goto(r, 1)}\033[K")
            elif old_scroll < scroll_end:
                for r in range(old_scroll + 1, scroll_end + 1):
                    _buf.append(f"{cursor_goto(r, 1)}\033[K")
    if resized:
        if old_scroll > scroll_end:
            for r in range(scroll_end + 1, min(old_scroll, height) + 1):
                _buf.append(f"{cursor_goto(r, 1)}\033[K")
        elif old_scroll < scroll_end:
            for r in range(old_scroll + 1, scroll_end + 1):
                _buf.append(f"{cursor_goto(r, 1)}\033[K")
        _buf.append(cursor_goto(scroll_end, 1) + cursor_save())
    else:
        _buf.append(cursor_goto(scroll_end, 1) + cursor_save())
    out.write(''.join(_buf))
    out.flush()


# ═══════════════════════════════════════════════════════════
# 光标区域切换（提取自 _BottomBar 方法）
# ═══════════════════════════════════════════════════════════

def _do_ensure_cursor_in_upper(bb: "_BottomBar") -> None:
    """确保光标位于滚动区域上边界。"""
    if not bb._active:
        return
    scroll_end = bb._last_scroll_end
    if scroll_end < 1:
        scroll_end = bb._term_height()
    sys.__stdout__.write(cursor_goto(scroll_end, 1))
    bb._cursor_tracker.set(scroll_end, 1)


def _do_ensure_cursor_in_lower(bb: "_BottomBar") -> None:
    """确保光标位于输入区域。"""
    if not bb._active:
        return
    with _try_acquire_output_lock(name="bottom_bar.ensure_cursor_in_lower", timeout=0.3) as locked:
        if not locked:
            return
        height = bb._term_height()
        term_w = bb._term_width()
        text = bb._last_rendered_text if bb._last_rendered_text else bb._last_text
        cursor_pos = min(bb._input_cursor_pos, len(text))
        if bb._input is not None:
            total = max(_BOTTOM_MIN_LINES, bb._last_bottom_lines)
            r_cursor, col, _, _ = bb._input.compute_cursor(
                text, cursor_pos, total,
                len(bb._subagent_lines), bb._completion.height,
            )
        else:
            max_input = max(1, term_w - 4)
            vis_row, vis_col = _compute_cursor_visual_pos(text, cursor_pos, max_input)
            total = max(_BOTTOM_MIN_LINES, bb._last_bottom_lines)
            subagent_offset = len(bb._subagent_lines)
            r_cursor = height - total + 4 + subagent_offset + bb._completion.height + vis_row
            r_cursor = max(1, min(r_cursor, height))
            col = min(3 + vis_col, term_w)
        sys.__stdout__.write(cursor_goto(r_cursor, col))
        sys.__stdout__.flush()
        bb._cursor_tracker.set(r_cursor, col)


# ═══════════════════════════════════════════════════════════
# SIGWINCH 注册（提取自 _BottomBar._register_sigwinch）
# ═══════════════════════════════════════════════════════════

def _do_register_sigwinch(bb: "_BottomBar") -> None:
    """注册 SIGWINCH 信号处理器。"""
    def _on_sigwinch(cols: int, rows: int) -> None:
        bb._width_cache.force_refresh()
        bb._needs_full_repaint = True
        if bb._request_redraw_cb is not None:
            try:
                bb._request_redraw_cb()
            except Exception:
                _logger.debug("SIGWINCH 回调触发 request_redraw 失败", exc_info=True)
    bb._sigwinch_cb = _on_sigwinch
    register_sigwinch_callback(bb._sigwinch_cb)


# ═══════════════════════════════════════════════════════════
# 生命周期（提取自 _BottomBar.setup / teardown）
# ═══════════════════════════════════════════════════════════

def _do_setup(bb: "_BottomBar") -> None:
    """设置底部栏。"""
    if bb._active:
        return
    height = bb._term_height()
    if height < _BOTTOM_MIN_HEIGHT:
        return
    bb._active = True
    _do_register_sigwinch(bb)
    # 显式行跟踪器：由装配层通过 RenderOutput.set_line_tracker 注入，
    # 不再全局劫持 sys.__stdout__（防御性兜底：未注入时创建实例）
    if bb._tracker is None:
        bb._tracker = _ST(sys.__stdout__)
    with _try_acquire_output_lock(name="bottom_bar.setup", timeout=1.0) as locked:
        if locked:
            bb._last_text = ""
            bb._last_bottom_lines = bb._bottom_lines
            scroll_end = height - bb._bottom_lines
            bb._last_scroll_end = scroll_end
            bb._last_sync_height = height
            bb._tracker.set_scroll_end(scroll_end)
            out = sys.__stdout__
            _buf = [
                cursor_save(),
                set_scroll_region(1, scroll_end),
                cursor_restore(),
                cursor_goto(scroll_end, 1) + cursor_save(),
                cursor_goto(height, 1),
            ]
            out.write(''.join(_buf))
            out.flush()
        else:
            sys.__stdout__.write("\n" + "\u2501" * 40 + "\n")
            sys.__stdout__.flush()


def _do_teardown(bb: "_BottomBar") -> None:
    """拆除底部栏。"""
    if not bb._active:
        return
    bb._active = False
    if bb._sigwinch_cb is not None:
        try:
            unregister_sigwinch_callback(bb._sigwinch_cb)
        except Exception:
            _logger.debug("注销 SIGWINCH 回调失败", exc_info=True)
        bb._sigwinch_cb = None
    # 显式行跟踪器：不再恢复 sys.__stdout__（未劫持），仅刷出历史落盘
    if bb._tracker is not None:
        try:
            bb._tracker._flush_history()
        except Exception:
            pass
        bb._tracker = None
    with _try_acquire_output_lock(name="bottom_bar.teardown", timeout=1.0) as locked:
        if locked:
            out = sys.__stdout__
            height = bb._term_height()
            start_row = max(1, height - bb._last_bottom_lines + 1)
            _buf = [reset_scroll_region(), cursor_save()]
            for r in range(start_row, height + 1):
                _buf.append(f"{cursor_goto(r, 1)}\033[K")
            _buf.append(cursor_restore())
            _buf.append(cursor_save())
            out.write(''.join(_buf))
            out.flush()
    bb._last_bottom_lines = _BOTTOM_MIN_LINES
    bb._last_height = 0
    bb._last_sync_height = 0


# ═══════════════════════════════════════════════════════════
# force_redraw 子函数（方向E·步骤10 拆分）
# ═══════════════════════════════════════════════════════════

def _build_separator_line(bb: "_BottomBar", r1: int, tw: int,
                          sep_start: int) -> list[str]:
    """构建分隔线 + 状态文本内嵌片段。

    方向E·步骤10 从 _do_force_redraw 拆出：纯字符串构建段，
    输出与原内联逻辑逐字符一致。sep_start 由主函数计算传入
    （narrow 分支不使用）。
    """
    buf: list[str] = []
    if _is_narrow():
        sep_len = min(tw - 2, 40)
        sep = f"{_COLOR_SEP}\u2501{_COLOR_RESET}" * sep_len
        buf.append(f"{cursor_goto(r1, 1)}  {sep}")
    else:
        # P1-4 修复：一次 snapshot 取全部状态字段（避免 5 次独立 property →
        # 5 次独立加锁快照，跨线程时字段可能来自不同时刻）；_build_status_text
        # 现接收 snap dict，从同一快照提取，保证
        # 「tool_count>0 用 tool_phase_start 否则用 main_phase_start」一致性。
        snap = bb._status.snapshot()
        status_text = (
            _build_status_text(snap) if snap.get("status_active") else ""
        )
        if snap.get("status_active") and status_text:
            status_colored = f"{_COLOR_ACCENT}{status_text}{_COLOR_RESET}"
            remaining = max(1, tw - 2 - _visual_width(status_text) - 1)
            sep = _build_gradient(remaining, start_color=sep_start)
            buf.append(f"{cursor_goto(r1, 1)}  {status_colored} {sep}")
        else:
            sep = _build_gradient(tw - 2, start_color=sep_start)
            buf.append(f"{cursor_goto(r1, 1)}  {sep}")
    return buf


def _build_subagent_lines(bb: "_BottomBar", subagent_lines: list[str], tw: int,
                          start: int) -> list[str]:
    """构建 subagent 面板行片段。

    方向E·步骤10 从 _do_force_redraw 拆出：逐行截断 + 光标定位，
    输出与原内联逻辑逐字符一致。
    """
    buf: list[str] = []
    for i, line in enumerate(subagent_lines):
        sr = start + i
        line = _ansi_truncate(line, tw)
        buf.append(f"{cursor_goto(sr, 1)}\033[K" + line)
    return buf


def _build_status_line(bb: "_BottomBar", r2: int, new_status: str) -> list[str]:
    """构建状态行片段。

    方向E·步骤10 从 _do_force_redraw 拆出：输出与原内联逻辑逐字符一致。
    （P2-12：删除未使用 tw 参数；状态行不做宽度截断，与原逻辑一致。）
    """
    return [f"{cursor_goto(r2, 1)}\033[K" + new_status]


# ═══════════════════════════════════════════════════════════
# force_redraw（提取自 _BottomBar.force_redraw）
# ═══════════════════════════════════════════════════════════

def _do_force_redraw(bb: "_BottomBar") -> None:
    """强制重绘底部栏全部内容。

    Args:
        bb: _BottomBar 实例。
    """
    if not bb._active:
        return
    bb._animator.tick()
    bb._update_system_stats()
    height = bb._term_height()
    with _try_acquire_output_lock(name="bottom_bar.force_redraw", timeout=1.0) as locked:
        if not locked:
            return
        try:
            text = bb._last_text
            with bb._subagent_lines_lock:
                subagent_lines = list(bb._subagent_lines)
            total = 2 + len(subagent_lines) + _compute_input_rows(text, bb._term_width(), bb._completion.height)
            new_status = bb._format_status()
            old_bottom_lines = bb._last_bottom_lines
            scroll_end = height - total
            delta = total - old_bottom_lines
            old_scroll_end = (
                (bb._last_height if bb._last_height > 0 else height) - old_bottom_lines
            )
            # P3-14：_last_status / _last_subagent_lines 为只写不读死字段，已删除
            # （原 bb._last_status = new_status / bb._last_subagent_lines = ... 移除）
            out = sys.__stdout__
            out.write(cursor_save())
            out.write(reset_scroll_region())
            bb._last_bottom_lines = total
            full_repaint = bb._needs_full_repaint
            bb._needs_full_repaint = False

            # SU 上滚
            if delta > 0 and old_scroll_end > 0 and not full_repaint:
                out.write(set_scroll_region(1, old_scroll_end))
                out.write(cursor_goto(old_scroll_end, 1))
                out.write(scroll_up(delta))
                out.write(reset_scroll_region())

            # 终端过小
            if scroll_end < 1:
                for r in range(1, height + 1):
                    out.write(f"{cursor_goto(r, 1)}\033[K")
                out.write(cursor_restore())
                out.write(cursor_goto(height, 1) + cursor_save())
                out.flush()
                bb._cursor_tracker.set(height, 1)
                bb._last_cursor_pos = bb._input_cursor_pos
                bb._last_height = height
                bb._last_scroll_end = height
                if bb._tracker is not None:
                    bb._tracker.set_scroll_end(height)
                return

            # 清除旧区域
            if full_repaint:
                clear_start = scroll_end + 1
            else:
                clear_start = max(old_scroll_end, scroll_end) + 1
            clear_end = height
            clear_buf: list[str] = []
            for r in range(clear_start, clear_end + 1):
                clear_buf.append(f"{cursor_goto(r, 1)}\033[K")
            if not full_repaint and bb._last_height > 0 and height < bb._last_height:
                for r in range(max(scroll_end + 1, 1), min(old_scroll_end, height) + 1):
                    clear_buf.append(f"{cursor_goto(r, 1)}\033[K")
            elif not full_repaint and bb._last_height > 0 and height > bb._last_height:
                for r in range(old_scroll_end + 1, scroll_end + 1):
                    clear_buf.append(f"{cursor_goto(r, 1)}\033[K")
            if full_repaint and scroll_end > old_scroll_end and bb._last_height > 0:
                for r in range(old_scroll_end + 1, scroll_end + 1):
                    clear_buf.append(f"{cursor_goto(r, 1)}\033[K")

            r1 = height - total + 1
            subagent_start = r1 + 1
            r2 = subagent_start + len(subagent_lines)
            tw = bb._term_width()

            # 分隔线 / subagent 面板 / 状态行（方向E·步骤10 拆分子函数，
            # 输出序列与原内联逻辑逐字符一致）
            sep_start = 45
            if bb._animator.breath_frame > 0:
                sep_start = bb._animator.sine_color(40, 45, 10)
            clear_buf.extend(_build_separator_line(bb, r1, tw, sep_start))
            clear_buf.extend(_build_subagent_lines(bb, subagent_lines, tw, subagent_start))
            clear_buf.extend(_build_status_line(bb, r2, new_status))
            out.write(''.join(clear_buf))

            # 输入行
            _draw_input_lines(bb, out, text, r2 + 1, tw)
            input_rows = bb._cached_input_rows

            # 清除底部残留 + 设置 DECSTBM
            for r in range(r2 + 1 + input_rows, height + 1):
                out.write(f"{cursor_goto(r, 1)}\033[K")
            bb._last_scroll_end = scroll_end
            if bb._tracker is not None:
                bb._tracker.set_scroll_end(scroll_end)
            out.write(set_scroll_region(1, scroll_end))
            if delta < 0 and old_scroll_end > 0 and not full_repaint:
                for r in range(old_scroll_end + 1, scroll_end + 1):
                    out.write(f"{cursor_goto(r, 1)}\033[K")
            out.write(cursor_restore())
            out.write(cursor_goto(scroll_end, 1) + cursor_save())
            out.flush()
            bb._last_cursor_pos = bb._input_cursor_pos
            bb._last_height = height
        except (OSError, ValueError, AttributeError):
            _logger.warning("force_redraw 写入失败", exc_info=True)
            try:
                sgr_reset()
            except Exception:
                pass
            return


__all__ = [
    "_build_gradient",
    "_build_separator_line",
    "_build_subagent_lines",
    "_build_status_line",
    "_do_sync_bottom_lines",
    "_do_ensure_cursor_in_upper",
    "_do_ensure_cursor_in_lower",
    "_do_register_sigwinch",
    "_do_setup",
    "_do_teardown",
    "_do_force_redraw",
]
