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
from src.tui._bottom_bar._layout_utils import (
    _is_narrow,
    _visual_width,
    _truncate_by_width,
)

if TYPE_CHECKING:
    from src.tui._cursor_tracker import CursorTracker


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

    # ── 正式方法接口（方向E·步骤10） ───────────────
    # _BottomBar.show_completions / hide_completions 委托此处，
    # 消除 _BottomBar 对 _CompletionPopup 私有字段的直改。

    def show(self, items: list[str], selected_idx: int, popup_height: int,
             title: str = "补全", texts: list[str] | None = None,
             start_pos: int = 0, orig_prefix: str = "",
             types: list[str] | None = None,
             match_prefix: str = "") -> None:
        """显示补全弹窗（封装全部字段赋值）。

        Args:
            items: 可见候选项（_BottomBar 已按高度裁剪）。
            selected_idx: 选中索引（已 clamp 到可见范围）。
            popup_height: 弹窗总高度（由 _BottomBar 计算传入）。
            title: 弹窗标题；非「补全」时视为选择模式（_is_selection）。
            texts: 完整候选项文本（可能多于可见 items）；缺省用 items。
            start_pos: 补全起始位置。
            orig_prefix: 原始前缀。
            types: 候选项类型列表。
            match_prefix: 匹配前缀。
        """
        self._popup_height = popup_height
        self._visible = True
        self._title = title
        self._is_selection = (title != "补全")
        self._items = list(items)
        self._texts = list(texts) if texts is not None else list(items)
        self._idx = selected_idx
        self._start_pos = start_pos
        self._orig_prefix = orig_prefix
        self._types = list(types) if types is not None else []
        self._match_prefix = match_prefix

    def hide(self) -> None:
        """隐藏弹窗：先保存 _last_idx_before_hide，再清空全部字段。"""
        if not self._visible:
            return
        saved_idx = self._idx
        self._last_idx_before_hide = saved_idx
        self.reset()

    def reset(self) -> None:
        """清空补全相关字段（hide 复用的纯清理，不保存 _last_idx_before_hide）。"""
        self._popup_height = 0
        self._visible = False
        self._title = "补全"
        self._is_selection = False
        self._items = []
        self._texts = []
        self._idx = 0
        self._start_pos = 0
        self._orig_prefix = ""
        self._types = []
        self._match_prefix = ""

    # ── 兼容字段写入正式方法（P2-11） ───────────────
    # _BottomBar._completion_idx / _completion_popup_height setter 委托此处，
    # 消除对 _CompletionPopup 私有字段的直接写（兼容测试/渲染读取路径）。

    def set_idx(self, value: int) -> None:
        """设置选中索引（兼容 _BottomBar._completion_idx setter 委托）。"""
        self._idx = value

    def set_popup_height(self, value: int) -> None:
        """设置弹窗高度（兼容 _BottomBar._completion_popup_height setter 委托）。"""
        self._popup_height = value

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
        self._write_items(out, r_start, cell_w, n)
        self._write_footer(out, r_start, n, total_items)
        return popup_height

    def render_cycle_update(self, out, popup_r_start: int, term_width: int) -> None:
        if not self._visible or not self._items:
            return
        self._animator.tick()
        n = len(self._items)
        popup_w = self._calc_popup_width(self._items, term_width)
        cell_w = popup_w - 3
        self._write_items(out, popup_r_start, cell_w, n)
        total_items = len(self._texts)
        self._write_footer(out, popup_r_start, n, total_items)

    # ── 渲染共享子方法（P3-16：render / render_cycle_update 去重） ──

    def _write_items(self, out, r_start: int, cell_w: int, n: int) -> None:
        """渲染候选项行（render / render_cycle_update 共享，逐字符一致）。"""
        types = self._types if len(self._types) == n else [""] * n
        for i, item in enumerate(self._items):
            r = r_start + 1 + i
            self._render_item_line(out, r, item, types[i], self._match_prefix, cell_w,
                                   is_selected=(i == self._idx))

    def _write_footer(self, out, r_start: int, n: int, total_items: int) -> None:
        """渲染底部提示行（render / render_cycle_update 共享，逐字符一致）。"""
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


__all__ = ["_CompletionPopup"]
