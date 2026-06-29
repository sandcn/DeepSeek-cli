"""底部栏绘制方法 — 从 _bottom_bar.py 提取的渲染函数。

职责范围：
  - 输入行绘制（_draw_input_lines_locked）
  - 全量底部栏绘制（_draw_all_locked）
  - 补全弹窗轻量重绘（_redraw_cycle_only）
  - 滚动区域调整（_apply_scroll_delta, _reclaim_scroll_back）

所有函数通过 `bar` 参数接收 _BottomBar 实例访问内部状态。
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from ._bottom_bar_blessed import (
    _blessed_cursor_goto,
    _blessed_move_clear,
    _blessed_restore_cursor,
    _blessed_save_cursor,
    _blessed_scroll_down,
    _blessed_scroll_up,
)
from ._bottom_bar_theme import (
    _BOTTOM_MIN_LINES,
    _COLOR_DEEP_CYAN,
    _COLOR_DIM,
    _COLOR_RESET,
    _COLOR_SEP,
    _MIN_INPUT_ROWS,
    _PLACEHOLDER_COMPACT,
    _PLACEHOLDER_STREAMING,
    _PLACEHOLDER_TEXT,
)
from ._bottom_cursor import (
    _expand_tabs,
    _wrap_by_width,
)

if TYPE_CHECKING:
    from ._bottom_bar import _BottomBar


__all__ = [
    "_draw_input_lines_locked",
    "_draw_all_locked",
    "_redraw_cycle_only",
    "_apply_scroll_delta",
    "_reclaim_scroll_back",
]


def _draw_input_lines_locked(
    bar: _BottomBar, out, text: str, r_start: int, term_width: int,
) -> None:
    """绘制输入行（需持有 output_lock），超长文本自动拆行。

    Args:
        bar: _BottomBar 实例。
        out: stdout 文件对象。
        text: 输入文本（空字符串显示占位提示）。
        r_start: 第一行输入区的行号（分隔线+状态行之后）。
        term_width: 当前终端宽度（由调用方传入，避免重复系统调用）。
    """
    max_input = max(1, term_width - 4)
    expanded = _expand_tabs(text)
    wrapped = _wrap_by_width(expanded, max_input)
    bar._cached_wrapped_for = text
    bar._cached_wrapped_width = max_input
    bar._cached_wrapped_lines = wrapped
    base_rows = max(_MIN_INPUT_ROWS, len(wrapped))
    bar._cached_input_rows = base_rows + bar._completion.height
    bar._last_rendered_text = text

    # ── 补全弹窗（委托 _CompletionPopup.render） ──
    bar._completion.render(out, r_start, term_width)
    popup_height = bar._completion.height

    # ── 输入文本行（在弹窗下方） ──
    text_start = r_start + popup_height
    for i, segment in enumerate(wrapped):
        r = text_start + i
        if i == 0:
            if text:
                out.write(_blessed_move_clear(r)
                          + f"{_COLOR_DEEP_CYAN}>{_COLOR_RESET}"
                          f" {segment}")
            else:
                if bar._status_active:
                    ph = _PLACEHOLDER_STREAMING
                    out.write(_blessed_move_clear(r)
                              + f"{_COLOR_DEEP_CYAN}>{_COLOR_RESET}"
                              f" {_COLOR_DIM}{ph}{_COLOR_RESET}")
                else:
                    ph = _PLACEHOLDER_COMPACT if bar._completion.is_visible else _PLACEHOLDER_TEXT
                    out.write(_blessed_move_clear(r)
                              + f"{_COLOR_DEEP_CYAN}>{_COLOR_RESET}"
                              f" {_COLOR_DIM}{ph}{_COLOR_RESET}")
        else:
            out.write(_blessed_move_clear(r)
                      + f"{_COLOR_DIM}\u00b7{_COLOR_RESET} {segment}")
        bar._cursor_tracker.set(r, 3)  # 提示符从第3列开始
    # ★ 填充剩余空白行，确保输入区至少 3 行
    for r in range(text_start + len(wrapped), text_start + 3):
        out.write(_blessed_move_clear(r) + "  ")
        bar._cursor_tracker.set(r, 1)


def _draw_all_locked(bar: _BottomBar, out, height: int) -> None:
    """绘制全部底部行（需持有 output_lock），超长文本自动拆行。

    布局（简约风）：
      第 1 行：左青右灰渐变分隔线（内容区与输入区的视觉边界）
      第 2 行：状态行（模型名·耗时·令牌数，青/灰两色）
      第 3 行起：青 ❯ <text>   （输入提示符 + 实时键入文本，超长拆行）
                 灰 · <text>    （续行，· 前缀）
                 （空输入时显示灰色占位提示）

    终端高度不足以容纳底部栏时跳过绘制。
    """
    total = bar._bottom_lines
    if height - total < 1:
        return
    bar._last_bottom_lines = total
    r1 = height - total + 1
    subagent_start = r1 + 1
    r2 = subagent_start + len(bar._subagent_lines)

    for r in range(r1, height + 1):
        out.write(_blessed_move_clear(r))

    tw = bar._term_width()
    sep_len = min(tw - 2, 40)
    sep = f"{_COLOR_SEP}\u2501{_COLOR_RESET}" * sep_len
    out.write(_blessed_cursor_goto(r1, 1) + "  " + sep)

    # ── subagent 面板行（在分隔线与状态行之间） ──
    for i, line in enumerate(bar._subagent_lines):
        sr = subagent_start + i
        out.write(_blessed_move_clear(sr) + line)

    status = bar._format_status()
    bar._last_status = status
    if status:
        out.write(_blessed_move_clear(r2) + status)

    text = bar._last_text or ""
    _draw_input_lines_locked(bar, out, text, r2 + 1, tw)


def _redraw_cycle_only(bar: _BottomBar) -> None:
    """仅重绘补全弹窗高亮变化（轻量路径，调用方须持有 output_lock）。

    与 force_redraw() 不同，此方法仅更新弹窗行的选中高亮
    和快捷键提示行，不重绘分隔线/状态行/输入区。

    由 render 线程在 CYCLE_COMPLETION 命令 handler 中调用。

    Args:
        bar: _BottomBar 实例。
    """
    if not bar._completion.is_visible or not bar._completion._items:
        return
    out = sys.__stdout__
    out.write(_blessed_save_cursor())
    height = bar._term_height()
    total = bar._bottom_lines
    popup_start = height - total + 3
    tw = bar._term_width()
    bar._completion.render_cycle_update(out, popup_start, tw)
    out.write(_blessed_restore_cursor())
    out.flush()
    bar._last_height = height


def _apply_scroll_delta(out, delta: int, old_scroll_end: int) -> None:
    """根据底部栏行数变化调整上屏内容滚动位置。

    ★ 自 2026-06-12 起 force_redraw() 不再调用此方法。
    SU 在 DECSTBM 区域内无 scrollback 缓冲，滚出顶部的行永久丢失，
    因此底部栏扩大时不再执行 SU，改为让弹窗直接覆盖底部内容区行。
    保留供将来可能的回退或替代方案使用（历史测试仍验证此方法）。

    delta > 0（底部栏扩大）：向上滚动内容腾出空间（SU）。
    delta <= 0 或 old_scroll_end < 1：无操作。

    参数:
        out: sys.__stdout__ 或等价的可写文件对象（TextIO）。
        delta: 底部栏行数变化量（新值 - 旧值）。
        old_scroll_end: 旧的 DECSTBM 滚动区域底部行号。
    """
    if delta <= 0 or old_scroll_end < 1:
        return
    out.write(_blessed_cursor_goto(old_scroll_end, 1))
    out.write(f"{_blessed_scroll_up(delta)}")


def _reclaim_scroll_back(out, delta: int, scroll_end: int) -> None:
    """缩小后在新 DECSTBM 内下滚内容以消除空白间隙。

    ★ 自 2026-06-12 起 force_redraw() 不再调用此方法。
    SD 下滚会产生顶部空白行，回收区域直接清除即可，由新输出自然填充。
    保留供将来可能的回退或替代方案使用。

    delta < 0（底部栏缩小）：在新 DECSTBM[1;scroll_end] 内做 SD 下滚。
    回收行（旧面板区域）无实际内容（已被清除），SD 仅产生顶部空白行，
    立即清除这些空行避免上屏出现多余空白行。

    参数:
        out: sys.__stdout__ 或等价的可写文件对象。
        delta: 底部栏行数变化量（新值 - 旧值，应为负数）。
        scroll_end: 新的 DECSTBM 滚动区域底部行号。
    """
    if delta >= 0 or scroll_end < 1:
        return
    n = -delta
    out.write(_blessed_cursor_goto(scroll_end, 1))
    out.write(f"{_blessed_scroll_down(n)}")
    # 清除 SD 下滚后在滚动区顶部产生的 n 行空行
    for r in range(1, min(n, scroll_end) + 1):
        out.write(_blessed_move_clear(r))
