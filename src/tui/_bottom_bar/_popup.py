"""补全弹窗模块 — _CompletionPopup 补全弹窗渲染。

从 ``_bottom_bar.py`` 提取为独立子模块。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.tui._screen import (
    _COLOR_COMPLETE_CMD_PREFIX,
    _COLOR_COMPLETE_DIR,
    _COLOR_COMPLETE_MATCH,
    _COLOR_COMPLETE_TITLE,
    _COLOR_DIM,
    _COLOR_RESET,
    _COLOR_SELECT_BG,
    _COLOR_SELECT_FG,
    _COLOR_TIME,
    cursor_goto,
)
from src.tui._animator import AnimatorContext

if TYPE_CHECKING:
    from src.tui._cursor_tracker import CursorTracker


# ═══════════════════════════════════════════════════════════
# 工具函数（内联自 _bottom_bar_old.py）
# ═══════════════════════════════════════════════════════════

def _is_narrow() -> bool:
    """判断是否为窄屏（宽度 < 60 列）。"""
    from src.tui._screen import _get_terminal_size
    w, _ = _get_terminal_size()
    return w < 60


def _visual_width(text: str) -> int:
    """计算字符串的可视宽度（去除 ANSI 转义序列）。"""
    from src.tui._screen import wcswidth_simple
    w = 0
    i = 0
    while i < len(text):
        if text[i] == '\033':
            j = i + 1
            if j < len(text) and text[j] == '[':
                j += 1
                while j < len(text) and text[j] not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz':
                    j += 1
                i = j + 1 if j < len(text) else len(text)
            elif j < len(text) and text[j] in ']PX^_':
                j += 1
                while j < len(text):
                    if text[j] == '\033' and j + 1 < len(text) and text[j + 1] == '\\':
                        i = j + 2
                        break
                    elif text[j] == '\a':
                        i = j + 1
                        break
                    j += 1
                else:
                    i = len(text)
            else:
                i = j + 1
        else:
            cw = wcswidth_simple(text[i])
            w += cw if cw >= 0 else 1
            i += 1
    return w


def _truncate_by_width(s: str, max_width: int) -> str:
    """按终端列宽截断字符串。"""
    from src.tui._screen import wcswidth_simple
    w = 0
    for i, ch in enumerate(s):
        cw = wcswidth_simple(ch) if wcswidth_simple(ch) >= 0 else 1
        if w + cw > max_width:
            return s[:i]
        w += cw
    return s


# ═══════════════════════════════════════════════════════════
# _CompletionPopup — 补全弹窗
# ═══════════════════════════════════════════════════════════

class _CompletionPopup:
    """补全弹窗 — 无边框扁平样式，在输入区顶部绘制。"""

    _COMPLETION_MAX_ITEMS = 999

    def __init__(self, cursor_tracker: "CursorTracker | None" = None,
                 animator: AnimatorContext | None = None):
        self._visible = False
        self._title = "补全"
        self._items: list[str] = []
        self._texts: list[str] = []
        self._start_pos: int = 0
        self._orig_prefix: str = ""
        self._types: list[str] = []
        self._match_prefix: str = ""
        self._is_selection: bool = False
        self._idx: int = 0
        self._last_idx_before_hide: int = 0
        self._popup_height: int = 0
        self._animator = animator or AnimatorContext.get_default()
        self._tracker = cursor_tracker

    @property
    def is_visible(self) -> bool:
        return self._visible

    @property
    def height(self) -> int:
        return self._popup_height

    @property
    def idx(self) -> int:
        return self._idx

    def cycle(self, delta: int = 1) -> int:
        if not self._visible or not self._items:
            return 0
        n = len(self._items)
        self._idx = (self._idx + delta) % n
        return self._idx

    def get_selected(self) -> tuple[str, int, str]:
        if not self._visible or not self._texts:
            return ("", 0, "")
        idx = min(self._idx, len(self._texts) - 1)
        return (self._texts[idx], self._start_pos, self._orig_prefix)

    @staticmethod
    def _calc_popup_width(items: list[str], term_width: int) -> int:
        if not items:
            return min(term_width - 2, 50)
        max_w = max(_visual_width(item) for item in items)
        return min(max(max_w + 4, 20), term_width - 2)

    def _render_item_line(self, out, r: int, item: str, item_type: str,
                          match_prefix: str, cell_w: int, is_selected: bool) -> None:
        truncated_raw = _truncate_by_width(item, cell_w)
        display = self._render_display_text(truncated_raw, item_type, match_prefix)
        pad = " " * max(0, cell_w - _visual_width(truncated_raw))
        if is_selected:
            if not _is_narrow():
                bg_color = self._animator.sine_color(235, 240, 10)
                bg_ansi = f"\033[48;5;{bg_color}m"
            else:
                bg_ansi = _COLOR_SELECT_BG
            out.write(
                f"{cursor_goto(r, 1)}\033[K"
                f" {bg_ansi}{_COLOR_SELECT_FG}\u25b6{_COLOR_RESET}"
                f"{bg_ansi}{_COLOR_SELECT_FG} {display}{pad}{_COLOR_RESET}"
            )
        else:
            out.write(f"{cursor_goto(r, 1)}\033[K  {display}{pad}")

    def _render_display_text(self, text: str, item_type: str, match_prefix: str) -> str:
        if item_type == "command" and text.startswith("/"):
            cmd_rest = text[1:]
            if match_prefix and len(match_prefix) > 1 and cmd_rest.startswith(match_prefix[1:]):
                inner = match_prefix[1:]
                matched = cmd_rest[:len(inner)]
                rest = cmd_rest[len(inner):]
                return (
                    f"{_COLOR_COMPLETE_CMD_PREFIX}/{_COLOR_RESET}"
                    f"{_COLOR_COMPLETE_MATCH}{matched}{_COLOR_RESET}{rest}"
                )
            return f"{_COLOR_COMPLETE_CMD_PREFIX}/{_COLOR_RESET}{cmd_rest}"
        if item_type == "dir" and text.endswith("/"):
            return f"{_COLOR_COMPLETE_DIR}{text}{_COLOR_RESET}"
        if item_type == "session":
            base = f"{_COLOR_TIME}{text}{_COLOR_RESET}"
        else:
            base = text
        if match_prefix and text.startswith(match_prefix):
            matched = text[:len(match_prefix)]
            rest = text[len(match_prefix):]
            if item_type == "session":
                return f"{_COLOR_TIME}{_COLOR_COMPLETE_MATCH}{matched}{_COLOR_RESET}{_COLOR_TIME}{rest}{_COLOR_RESET}"
            return f"{_COLOR_COMPLETE_MATCH}{matched}{_COLOR_RESET}{rest}"
        return base

    def render(self, out, r_start: int, term_width: int) -> int:
        popup_height = self._popup_height
        if popup_height <= 0 or not self._items:
            return 0
        popup_w = self._calc_popup_width(self._items, term_width)
        n = len(self._items)
        total_items = len(self._texts)
        if not _is_narrow():
            title_color = self._animator.sine_color(45, 81, 12)
            title_ansi = f"\033[1;38;5;{title_color}m"
        else:
            title_ansi = _COLOR_COMPLETE_TITLE
        header = f" {title_ansi}{self._title}{_COLOR_RESET} {_COLOR_DIM}({total_items}项){_COLOR_RESET}"
        out.write(f"{cursor_goto(r_start, 1)}\033[K" + header)
        cell_w = popup_w - 3
        types = self._types if len(self._types) == n else [""] * n
        for i, item in enumerate(self._items):
            r = r_start + 1 + i
            self._render_item_line(out, r, item, types[i], self._match_prefix, cell_w,
                                   is_selected=(i == self._idx))
        footer_r = r_start + 1 + n
        truncated = total_items > n
        is_selection = self._is_selection
        hint_prefix = "\u2191\u2193 Enter Esc" if is_selection else "Tab \u2191\u2193 Esc"
        if truncated:
            hint = (f" {_COLOR_TIME}{self._idx + 1}/{n}{_COLOR_RESET}"
                    f" {_COLOR_DIM}(\u524d{n}/{total_items}){_COLOR_RESET}  {hint_prefix} ")
        else:
            hint = f" {hint_prefix} "
        if not _is_narrow():
            dot_color = self._animator.sine_color(45, 81, 12)
            hint_dot = f" \033[38;5;{dot_color}m\u25c9{_COLOR_RESET}"
        else:
            hint_dot = ""
        out.write(f"{cursor_goto(footer_r, 1)}\033[K" + f"{_COLOR_DIM}{hint}{_COLOR_RESET}{hint_dot}")
        return popup_height

    def render_cycle_update(self, out, popup_r_start: int, term_width: int) -> None:
        if not self._visible or not self._items:
            return
        self._animator.tick()
        n = len(self._items)
        popup_w = self._calc_popup_width(self._items, term_width)
        cell_w = popup_w - 3
        types = self._types if len(self._types) == n else [""] * n
        for i, item in enumerate(self._items):
            r = popup_r_start + 1 + i
            self._render_item_line(out, r, item, types[i], self._match_prefix, cell_w,
                                   is_selected=(i == self._idx))
        total_items = len(self._texts)
        footer_r = popup_r_start + 1 + n
        truncated = total_items > n
        is_selection = self._is_selection
        hint_prefix = "\u2191\u2193 Enter Esc" if is_selection else "Tab \u2191\u2193 Esc"
        if truncated:
            hint = (f" {_COLOR_TIME}{self._idx + 1}/{n}{_COLOR_RESET}"
                    f" {_COLOR_DIM}(\u524d{n}/{total_items}){_COLOR_RESET}  {hint_prefix} ")
        else:
            hint = f" {hint_prefix} "
        if not _is_narrow():
            dot_color = self._animator.sine_color(45, 81, 12)
            hint_dot = f" \033[38;5;{dot_color}m\u25c9{_COLOR_RESET}"
        else:
            hint_dot = ""
        out.write(f"{cursor_goto(footer_r, 1)}\033[K" + f"{_COLOR_DIM}{hint}{_COLOR_RESET}{hint_dot}")


__all__ = ["_CompletionPopup"]
