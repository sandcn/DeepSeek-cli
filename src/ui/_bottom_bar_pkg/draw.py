"""底部栏绘制方法 — 从 _bottom_bar.py 提取的渲染函数。

职责范围：
  - 输入行绘制（_draw_input_lines_locked）
  - 全量底部栏绘制（_draw_all_locked）
  - 补全弹窗轻量重绘（_redraw_cycle_only）
  （无该项）

所有函数通过 `bar` 参数接收 _BottomBar 实例访问内部状态。
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from .blessed import (
    _blessed_cursor_goto,
    _blessed_move_clear,
    _blessed_restore_cursor,
    _blessed_save_cursor,
    _blessed_scroll_down,
    _blessed_scroll_up,
)
from .theme import (
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
from .cursor import (
    _expand_tabs,
    _wrap_by_width,
)

if TYPE_CHECKING:
    from .bar import _BottomBar


__all__ = [
    "_draw_input_lines_locked",
    "_draw_all_locked",
    "_redraw_cycle_only",
]


def _draw_input_lines_locked(
    bar: _BottomBar, out, text: str, r_start: int, term_width: int,
) -> None:
    """绘制输入行（需持有 output_lock），超长文本自动拆行。

    性能优化：将所有 ANSI 序列收集到缓冲区后一次写入，
    减少高频循环中的独立 write() 系统调用次数。

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
    # ★ 性能优化：批量收集 ANSI 序列，一次 write
    buf: list[str] = []
    for i, segment in enumerate(wrapped):
        r = text_start + i
        if i == 0:
            if text:
                buf.append(_blessed_move_clear(r)
                           + f"{_COLOR_DEEP_CYAN}>{_COLOR_RESET}"
                           f" {segment}")
            else:
                if bar._status_active:
                    ph = _PLACEHOLDER_STREAMING
                    buf.append(_blessed_move_clear(r)
                               + f"{_COLOR_DEEP_CYAN}>{_COLOR_RESET}"
                               f" {_COLOR_DIM}{ph}{_COLOR_RESET}")
                else:
                    ph = _PLACEHOLDER_COMPACT if bar._completion.is_visible else _PLACEHOLDER_TEXT
                    buf.append(_blessed_move_clear(r)
                               + f"{_COLOR_DEEP_CYAN}>{_COLOR_RESET}"
                               f" {_COLOR_DIM}{ph}{_COLOR_RESET}")
        else:
            buf.append(_blessed_move_clear(r)
                       + f"{_COLOR_DIM}\u00b7{_COLOR_RESET} {segment}")
        bar._cursor_tracker.set(r, 3)  # 提示符从第3列开始
    # ★ 填充剩余空白行，确保输入区至少 3 行
    for r in range(text_start + len(wrapped), text_start + 3):
        buf.append(_blessed_move_clear(r) + "  ")
        bar._cursor_tracker.set(r, 1)
    if buf:
        out.write(''.join(buf))


def _draw_all_locked(bar: _BottomBar, out, height: int) -> None:
    """绘制全部底部行（需持有 output_lock），超长文本自动拆行。

    布局（简约风）：
      第 1 行：左青右灰渐变分隔线（内容区与输入区的视觉边界）
      第 2 行：状态行（模型名·耗时·令牌数，青/灰两色）
      第 3 行起：青 ❯ <text>   （输入提示符 + 实时键入文本，超长拆行）
                 灰 · <text>    （续行，· 前缀）
                 （空输入时显示灰色占位提示）

    终端高度不足以容纳底部栏时跳过绘制。

    性能优化：批量收集 ANSI 序列后一次写入，减少独立 write() 次数。
    """
    total = bar._bottom_lines
    if height - total < 1:
        return
    bar._last_bottom_lines = total
    r1 = height - total + 1
    subagent_start = r1 + 1
    r2 = subagent_start + len(bar._subagent_lines)

    # ★ 批量收集清行序列
    buf: list[str] = []
    for r in range(r1, height + 1):
        buf.append(_blessed_move_clear(r))

    tw = bar._term_width()
    sep_len = min(tw - 2, 40)
    sep = f"{_COLOR_SEP}\u2501{_COLOR_RESET}" * sep_len
    buf.append(_blessed_cursor_goto(r1, 1) + "  " + sep)

    # ── subagent 面板行（在分隔线与状态行之间） ──
    for i, line in enumerate(bar._subagent_lines):
        sr = subagent_start + i
        buf.append(_blessed_move_clear(sr) + line)

    status = bar._format_status()
    bar._last_status = status
    if status:
        buf.append(_blessed_move_clear(r2) + status)

    if buf:
        out.write(''.join(buf))

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
