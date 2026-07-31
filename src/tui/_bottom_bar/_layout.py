"""底部栏布局模块 — 输入行绘制、布局计算、工具函数。

从 ``_bottom_bar.py`` 提取为独立子模块。

包含：
  - 布局常量
  - 工具函数（_is_narrow / _visual_width / _truncate_by_width 真源在 _layout_utils.py，
    本文件导入并 re-export；_ansi_truncate 本地实现）
  - 输入行绘制（_draw_input_lines）
  - 布局计算（_compute_input_rows / _compute_bottom_lines_for）
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from src.tui._screen import (
    _COLOR_ACCENT,
    _COLOR_DEEP_CYAN,
    _COLOR_DIM,
    _COLOR_RESET,
    _COLOR_SEP,
    _COLOR_SPEED,
    _COLOR_TIME,
    cursor_goto,
    wcswidth_simple,
)
from src.tui._animator import AnimatorContext
from src.tui._bottom_bar._layout_utils import (
    _is_narrow,
    _visual_width,
    _truncate_by_width,
)
from src.tui._input import (
    _TAB_WIDTH,  # 唯一真源在 _input.py（步骤 5.1 统一常量漂移，re-export 兼容）
    _expand_tabs,
    _wrap_by_width,
)

if TYPE_CHECKING:
    from src.tui._bottom_bar._bar import _BottomBar

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# 布局常量
# ═══════════════════════════════════════════════════════════

_BOTTOM_MIN_HEIGHT = 12
_BOTTOM_MIN_LINES = 5
_MIN_INPUT_ROWS = 1

_PLACEHOLDER_TEXT = "输入消息 · /help 查看命令 · Ctrl+N 切换模型 · Tab 补全"
_PLACEHOLDER_COMPACT = "/help · Ctrl+N · Tab"
_PLACEHOLDER_STREAMING = "AI 生成中..."


# ═══════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════
# _is_narrow / _visual_width / _truncate_by_width 真源已提取至
# _layout_utils.py（步骤 4.4），本文件顶部导入并 re-export。

def _ansi_truncate(s: str, max_width: int) -> str:
    """按终端列宽截断字符串（ANSI 转义序列感知）。"""
    if max_width <= 0 or not s:
        return ""
    w = 0
    parts: list[str] = []
    has_ansi = False
    i = 0
    while i < len(s):
        if s[i] == '\033':
            seq_start = i
            j = i + 1
            if j < len(s) and s[j] == '[':
                j += 1
                while j < len(s) and s[j] not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz':
                    j += 1
                end = j + 1 if j < len(s) else len(s)
            elif j < len(s) and s[j] in ']PX^_':
                j += 1
                while j < len(s):
                    if s[j] == '\033' and j + 1 < len(s) and s[j + 1] == '\\':
                        end = j + 2
                        break
                    elif s[j] == '\a':
                        end = j + 1
                        break
                    j += 1
                else:
                    end = len(s)
            else:
                end = j + 1
            parts.append(s[seq_start:end])
            has_ansi = True
            i = end
            continue
        cw = wcswidth_simple(s[i])
        cw = cw if cw >= 0 else 1
        if w + cw > max_width:
            if has_ansi:
                parts.append('\033[0m')
            return ''.join(parts)
        w += cw
        parts.append(s[i])
        i += 1
    return ''.join(parts)


def _build_glow_ansi(frame: int, base_color: int, amplitude: int) -> str:
    """构建呼吸辉光 ANSI 前景色序列。"""
    animator = AnimatorContext.get_default()
    color = animator.sine_color(base_color, base_color + amplitude, 12)
    return f"\033[38;5;{color}m"


# ═══════════════════════════════════════════════════════════
# 布局计算（提取自 _BottomBar 方法）
# ═══════════════════════════════════════════════════════════

def _compute_input_rows(text: str, term_width: int, completion_height: int) -> int:
    """计算输入区域行数。"""
    if not text:
        base = _MIN_INPUT_ROWS
    else:
        max_input = max(1, term_width - 4)
        expanded = _expand_tabs(text)
        wrapped = _wrap_by_width(expanded, max_input)
        base = max(_MIN_INPUT_ROWS, len(wrapped))
    return 2 + base + completion_height


def _compute_bottom_lines_for(text: str, term_width: int,
                               subagent_lines_count: int, completion_height: int) -> int:
    """计算底部栏总行数。"""
    if not text:
        base = _MIN_INPUT_ROWS
    else:
        max_input = max(1, term_width - 4)
        expanded = _expand_tabs(text)
        wrapped = _wrap_by_width(expanded, max_input)
        base = max(_MIN_INPUT_ROWS, len(wrapped))
    return 4 + subagent_lines_count + base + completion_height


# ═══════════════════════════════════════════════════════════
# 输入行绘制（提取自 _BottomBar._draw_input_lines）
# ═══════════════════════════════════════════════════════════

def _draw_input_lines(bb: "_BottomBar", out, text: str, r_start: int, term_width: int) -> None:
    """绘制输入行（含补全弹窗、CPU/MEM 行、输入文本行、时间戳行）。

    Args:
        bb: _BottomBar 实例。
        out: 输出流。
        text: 当前输入文本。
        r_start: 起始行号。
        term_width: 终端宽度。
    """
    max_input = max(1, term_width - 4)
    expanded = _expand_tabs(text)
    wrapped = _wrap_by_width(expanded, max_input)
    bb._cached_wrapped_for = text
    bb._cached_wrapped_width = max_input
    bb._cached_wrapped_lines = wrapped
    base_rows = max(_MIN_INPUT_ROWS, len(wrapped))
    bb._cached_input_rows = base_rows + bb._completion.height + 2
    bb._last_rendered_text = text

    # 补全弹窗
    bb._completion.render(out, r_start, term_width)
    popup_height = bb._completion.height
    text_start = r_start + popup_height

    # 上分割线（CPU/MEM）
    cpu_int = max(0, min(100, round(bb._cached_cpu_percent)))
    mem_int = max(0, min(100, round(bb._cached_mem_percent)))
    cpu_mem_info = (
        f" {_COLOR_ACCENT}CPU:{_COLOR_RESET}"
        f" {_COLOR_SPEED}{cpu_int}{_COLOR_ACCENT}%{_COLOR_RESET}"
        f" {_COLOR_DIM}\u00b7{_COLOR_RESET} "
        f"{_COLOR_ACCENT}MEM:{_COLOR_RESET}"
        f" {_COLOR_SPEED}{mem_int}{_COLOR_ACCENT}%{_COLOR_RESET}"
    )
    cpu_mem_w = _visual_width(cpu_mem_info)
    top_sep = f"{_COLOR_SEP}\u2501{_COLOR_RESET}" * max(1, term_width - cpu_mem_w) + cpu_mem_info
    out.write(f"{cursor_goto(text_start, 1)}\033[K" + top_sep)

    # 输入文本行
    buf: list[str] = []
    for i, segment in enumerate(wrapped):
        r = text_start + 1 + i
        if i == 0:
            if _is_narrow():
                prompt_color = _COLOR_DEEP_CYAN
                prompt_prefix = f"{prompt_color}>{_COLOR_RESET} "
            else:
                prompt_color = _build_glow_ansi(bb._animator.breath_frame, 32, 49)
                prompt_prefix = f"{prompt_color}>{_COLOR_RESET} "
            if text:
                buf.append(f"{cursor_goto(r, 1)}\033[K" + prompt_prefix + segment)
            else:
                if _is_narrow():
                    placeholder_color = _COLOR_DIM
                else:
                    placeholder_color = _build_glow_ansi(bb._animator.breath_frame, 242, 10)
                if bb._status_active:
                    ph = _PLACEHOLDER_STREAMING
                    buf.append(f"{cursor_goto(r, 1)}\033[K" + prompt_prefix + f"{placeholder_color}{ph}\033[0m")
                else:
                    ph = _PLACEHOLDER_COMPACT if bb._completion.is_visible else _PLACEHOLDER_TEXT
                    buf.append(f"{cursor_goto(r, 1)}\033[K" + prompt_prefix + f"{placeholder_color}{ph}\033[0m")
        else:
            buf.append(f"{cursor_goto(r, 1)}\033[K" + f"{_COLOR_DIM}\u00b7{_COLOR_RESET} {segment}")

    # 下分割线（时间戳）
    now_local = time.localtime()
    ts = (
        f"{now_local.tm_year}-{now_local.tm_mon:02d}-"
        f"{now_local.tm_mday:02d} {now_local.tm_hour:02d}:"
        f"{now_local.tm_min:02d}:{now_local.tm_sec:02d}"
    )
    time_info = f" {_COLOR_DIM}{ts}{_COLOR_RESET}"
    time_w = _visual_width(time_info)
    bottom_sep = f"{_COLOR_SEP}\u2501{_COLOR_RESET}" * max(1, term_width - time_w) + time_info
    bottom_sep_row = text_start + 1 + base_rows
    buf.append(f"{cursor_goto(bottom_sep_row, 1)}\033[K" + bottom_sep)
    if buf:
        out.write(''.join(buf))


__all__ = [
    "_BOTTOM_MIN_HEIGHT", "_BOTTOM_MIN_LINES", "_MIN_INPUT_ROWS", "_TAB_WIDTH",
    "_PLACEHOLDER_TEXT", "_PLACEHOLDER_COMPACT", "_PLACEHOLDER_STREAMING",
    "_is_narrow", "_visual_width", "_truncate_by_width", "_ansi_truncate",
    "_build_glow_ansi",
    "_compute_input_rows", "_compute_bottom_lines_for", "_draw_input_lines",
]
