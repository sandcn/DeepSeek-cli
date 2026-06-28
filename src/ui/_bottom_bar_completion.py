"""_BottomBar 补全弹窗 — 独立 _CompletionPopup 类。

从 _bottom_bar.py 提取，_BottomBar 通过组合使用。
提供 show/hide/cycle/get_selected + render() 绘制方法。

依赖 _bottom_bar_theme 中的颜色常量和 _bottom_cursor 中的纯函数。
使用 Blessed Terminal.move_xy / Terminal.clear_eol 替代原始 ANSI 序列。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._blessed import get_terminal
from ._bottom_bar_theme import (
    _COLOR_COMPLETE_CMD_PREFIX,
    _COLOR_COMPLETE_DIR,
    _COLOR_COMPLETE_MATCH,
    _COLOR_COMPLETE_TITLE,
    _COLOR_DIM,
    _COLOR_RESET,
    _COLOR_SELECT_BG,
    _COLOR_SELECT_FG,
    _COLOR_TIME,
)
from ._bottom_cursor import _truncate_by_width, _visual_len

if TYPE_CHECKING:
    from ._cursor_tracker import CursorTracker


class _CompletionPopup:
    """补全弹窗 — 无边框扁平样式，在输入区顶部绘制。

    视觉（无边框扁平样式）：
      {title} (N项)            ← 标题行
        ▶ 选中项              ← ▶ 指示器 + 高亮背景
          普通项              ← 缩进对齐
      ↑↓/Enter/Esc            ← 快捷键提示

    show()/hide() 由 _BottomBar 调用，内部通过 redraw_callback
    触发全量重绘。
    """

    _COMPLETION_MAX_ITEMS = 10      # 单屏最多显示多少条

    def __init__(self, cursor_tracker: "CursorTracker | None" = None):
        self._visible = False
        self._title = "补全"              # 弹窗标题前缀
        self._items: list[str] = []      # 显示文本
        self._texts: list[str] = []       # 替换文本（可能与显示不同）
        self._start_pos: int = 0          # 从光标前多少字符开始替换
        self._orig_prefix: str = ""       # 原始前缀（用于重建替换）
        self._types: list[str] = []       # 候选项类型列表（command/dir/file/param/session）
        self._match_prefix: str = ""       # 用于匹配高亮的前缀
        self._is_selection: bool = False  # 是否为选择模式
        self._idx: int = 0               # 当前选中索引
        self._popup_height: int = 0      # 弹窗所占行数
        self._tracker = cursor_tracker

    @staticmethod
    def _calc_popup_width(items: list[str], term_width: int) -> int:
        """根据候选项动态计算弹窗宽度。

        取最长候选项的可视宽度 + 4 边距，
        再 bound 到 [20, term_width - 2]。

        Args:
            items: 候选项显示文本列表。
            term_width: 终端宽度。

        Returns:
            弹窗宽度（列数）。
        """
        if not items:
            return min(term_width - 2, 50)
        max_w = max(_visual_len(item) for item in items)
        return min(max(max_w + 4, 20), term_width - 2)

    # ── 公开属性（供 _BottomBar 和外部调用方访问） ──────────

    @property
    def is_visible(self) -> bool:
        """补全弹窗是否可见。"""
        return self._visible

    @property
    def height(self) -> int:
        """弹窗所占行数（弹窗不可见时为 0）。"""
        return self._popup_height

    @property
    def idx(self) -> int:
        """当前选中索引。"""
        return self._idx

    # ── 兼容 property（供外部直读私有属性的调用方） ──────────

    @property
    def _completion_visible(self) -> bool:
        return self._visible

    @property
    def _completion_title(self) -> str:
        return self._title

    @property
    def _completion_items(self) -> list[str]:
        return self._items

    @property
    def _completion_texts(self) -> list[str]:
        return self._texts

    @property
    def _completion_start_pos(self) -> int:
        return self._start_pos

    @property
    def _completion_orig_prefix(self) -> str:
        return self._orig_prefix

    @property
    def _completion_is_selection(self) -> bool:
        return self._is_selection

    @property
    def _completion_idx(self) -> int:
        return self._idx

    @property
    def _completion_popup_height(self) -> int:
        return self._popup_height

    # ── 生命周期 ──────────────────────────────────────────

    def cycle(self, delta: int = 1) -> int:
        """循环切换选中项，返回新索引。

        Args:
            delta: +1 下一项，-1 上一项。

        Returns:
            新的选中索引。
        """
        if not self._visible or not self._items:
            return 0

        n = len(self._items)
        self._idx = (self._idx + delta) % n
        return self._idx

    def get_selected(self) -> tuple[str, int, str]:
        """获取当前选中补全项的数据。

        Returns:
            (replacement_text, start_pos, orig_prefix) 三元组。
        """
        if not self._visible or not self._texts:
            return ("", 0, "")
        idx = min(self._idx, len(self._texts) - 1)
        return (
            self._texts[idx],
            self._start_pos,
            self._orig_prefix,
        )

    @staticmethod
    def _render_item_line(
        out, r: int, item: str, item_type: str,
        match_prefix: str, cell_w: int, is_selected: bool,
    ) -> None:
        """渲染单行候选项（含类型颜色和匹配高亮）。

        Args:
            out: stdout 文件对象。
            r: 行号（1-based）。
            item: 显示文本。
            item_type: 候选项类型（command/dir/file/param/session/""）。
            match_prefix: 匹配前缀（用于高亮，空字符串时不高亮）。
            cell_w: 单元格宽度（列数）。
            is_selected: 是否为当前选中项。
        """
        try:
            term = get_terminal()
            move_clear = lambda rr: term.move_xy(0, rr - 1) + term.clear_eol()
        except Exception:
            move_clear = lambda rr: f"\033[{rr};1H\033[K"

        # 先截断，确保不超宽（取原始截断文本用于宽度计算）
        truncated_raw = _truncate_by_width(item, cell_w)
        # 应用类型颜色和匹配高亮
        display = _render_display_text(truncated_raw, item_type, match_prefix, cell_w)
        pad = " " * max(0, cell_w - _visual_len(truncated_raw))

        if is_selected:
            out.write(move_clear(r)
                      + f" {_COLOR_SELECT_BG}{_COLOR_SELECT_FG}\u25b6{_COLOR_RESET}"
                      f"{_COLOR_SELECT_BG}{_COLOR_SELECT_FG} {display}{pad}{_COLOR_RESET}")
        else:
            out.write(move_clear(r)
                      + f"  {display}{pad}")

    # ── 增量绘制（仅弹窗行，不含分隔线/状态行/输入区） ──────

    def render(self, out, r_start: int, term_width: int) -> int:
        """绘制补全弹窗（仅弹窗部分），返回绘制的行数。

        使用 Blessed Terminal.move_xy 和 clear_eol 替代原始 ANSI 序列。

        Args:
            out: stdout 文件对象。
            r_start: 弹窗起始行号（输入区第一行）。
            term_width: 终端宽度。

        Returns:
            绘制的行数（0 表示弹窗不可见，未绘制任何内容）。
        """
        popup_height = self._popup_height
        if popup_height <= 0 or not self._items:
            return 0

        try:
            term = get_terminal()
            move_clear = lambda r: term.move_xy(0, r - 1) + term.clear_eol()
        except Exception:
            move_clear = lambda r: f"\033[{r};1H\033[K"

        popup_w = self._calc_popup_width(self._items, term_width)
        n = len(self._items)

        # ── 标题行 ──
        total_items = len(self._texts)
        header = f" {_COLOR_COMPLETE_TITLE}{self._title}{_COLOR_RESET} {_COLOR_DIM}({total_items}项){_COLOR_RESET}"
        out.write(move_clear(r_start) + header)
        if self._tracker:
            self._tracker.set(r_start, 1)

        # ── 选项行 ──
        cell_w = popup_w - 3
        types = self._types if len(self._types) == n else [""] * n
        for i, item in enumerate(self._items):
            r = r_start + 1 + i
            self._render_item_line(
                out, r, item, types[i],
                self._match_prefix, cell_w,
                is_selected=(i == self._idx),
            )
            if self._tracker:
                self._tracker.set(r, 1)

        # ── 快捷键提示行 ──
        footer_r = r_start + 1 + n
        truncated = total_items > n
        is_selection = self._is_selection
        if is_selection:
            hint_prefix = "\u2191\u2193 Enter Esc"
        else:
            hint_prefix = "Tab \u2191\u2193 Esc"
        if truncated:
            hint = f" {_COLOR_TIME}{self._idx + 1}/{n}{_COLOR_RESET} {_COLOR_DIM}(\u524d{n}/{total_items}){_COLOR_RESET}  {hint_prefix} "
        else:
            hint = f" {hint_prefix} "
        out.write(move_clear(footer_r) + f"{_COLOR_DIM}{hint}{_COLOR_RESET}")
        if self._tracker:
            self._tracker.set(footer_r, 1)

        return popup_height

    def render_cycle_update(self, out, popup_r_start: int, term_width: int) -> None:
        """增量更新选项行和底部快捷键提示（cycle 时使用，仅重绘弹窗行）。

        Args:
            out: stdout 文件对象。
            popup_r_start: 弹窗第一行（标题行）的行号。
            term_width: 终端宽度。
        """
        if not self._visible or not self._items:
            return

        try:
            term = get_terminal()
            move_clear = lambda r: term.move_xy(0, r - 1) + term.clear_eol()
        except Exception:
            move_clear = lambda r: f"\033[{r};1H\033[K"

        n = len(self._items)
        popup_w = self._calc_popup_width(self._items, term_width)
        cell_w = popup_w - 3
        types = self._types if len(self._types) == n else [""] * n

        for i, item in enumerate(self._items):
            r = popup_r_start + 1 + i
            self._render_item_line(
                out, r, item, types[i],
                self._match_prefix, cell_w,
                is_selected=(i == self._idx),
            )
            if self._tracker:
                self._tracker.set(r, 1)

        # ── 快捷键提示行 ──
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
        out.write(move_clear(footer_r) + f"{_COLOR_DIM}{hint}{_COLOR_RESET}")
        if self._tracker:
            self._tracker.set(footer_r, 1)


def _render_display_text(text: str, item_type: str, match_prefix: str, cell_w: int) -> str:
    """将候选项显示文本渲染为带类型颜色和匹配高亮的 ANSI 字符串。

    注意：调用方应确保 text 已经截断（通过 _truncate_by_width），
    本函数不再重复截断，以保持与 _render_item_line 中宽度计算一致。

    处理顺序：先类型着色（整体包裹），再对类型色区域内做匹配高亮。
    命令项特殊：/ 前缀独立着色，剩余部分做匹配高亮。

    Args:
        text: 已截断的显示文本（原始文本，不含 ANSI 序列）。
        item_type: 候选项类型。
        match_prefix: 匹配前缀。
        cell_w: 单元格宽度（用于匹配高亮的边界判断）。

    Returns:
        带 ANSI 颜色序列的显示字符串。
    """
    # ── 命令项特殊处理：/ 前缀独立着色 + 剩余部分匹配高亮 ──
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
        # 仅 / 前缀着色，无匹配高亮
        return f"{_COLOR_COMPLETE_CMD_PREFIX}/{_COLOR_RESET}{cmd_rest}"

    # ── 目录项：整体蓝灰包裹 ──
    if item_type == "dir" and text.endswith("/"):
        return f"{_COLOR_COMPLETE_DIR}{text}{_COLOR_RESET}"

    # ── 匹配高亮（文件/参数/会话/普通项，先着色再高亮） ──
    if item_type == "session":
        base = f"{_COLOR_TIME}{text}{_COLOR_RESET}"
    else:
        base = text

    if match_prefix and text.startswith(match_prefix):
        matched = text[:len(match_prefix)]
        rest = text[len(match_prefix):]
        # 在已有类型色包裹的基础上，对匹配部分做高亮
        if item_type == "session":
            return f"{_COLOR_TIME}{matched[:0]}{_COLOR_COMPLETE_MATCH}{matched}{_COLOR_RESET}{_COLOR_TIME}{rest}{_COLOR_RESET}"
        return f"{_COLOR_COMPLETE_MATCH}{matched}{_COLOR_RESET}{rest}"

    return base
