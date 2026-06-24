"""输入行渲染器 — 底部栏输入行的拆行渲染 + 光标视觉位置计算。

从 _bottom_bar.py 拆分，负责：
  - _draw_input_lines_locked — 绘制输入文本行（含占位符、续行、补全弹窗）
  - _cursor_visual_pos_from_cache — 光标视觉位置缓存计算
  - compute_cursor_position — 公开 API，供 RenderEngine 定位
  - get_cursor_info — 获取光标定位所需数据快照
  - _compute_input_rows — 动态行数计算
  - 缓存字段管理（wrapped 行、输入行数、最后渲染文本）
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from wcwidth import wcswidth

from ._scroll_region import blessed_move_clear
from ._bottom_bar_theme import (
    _COLOR_DEEP_CYAN,
    _COLOR_DIM,
    _COLOR_RESET,
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
    from ._bottom_bar_completion import _CompletionPopup
    from ._scroll_region import _term_height, _term_width


class InputRenderer:
    """输入行渲染器 — 管理输入文本的拆行渲染和光标计算。

    所有方法依赖注入 shared mutable state（通过参数传入），
    不持有 _BottomBar 的内部字段引用，确保职责单一。
    """

    def __init__(self):
        # 缓存字段
        self._cached_wrapped_for: str = ""
        self._cached_wrapped_width: int = 0
        self._cached_wrapped_lines: list[str] | None = None
        self._cached_input_rows: int = _MIN_INPUT_ROWS
        self._last_rendered_text: str = ""

    # ── 只读属性（供 _BottomBar 访问） ──
    @property
    def cached_input_rows(self) -> int:
        return self._cached_input_rows

    @property
    def last_rendered_text(self) -> str:
        return self._last_rendered_text

    def compute_input_rows(self, text: str, term_width: int, popup_height: int) -> int:
        """根据当前输入文本计算所需的输入行数（最少 3 行 + 弹窗高度）。"""
        if not text:
            base = _MIN_INPUT_ROWS
        else:
            max_input = max(1, term_width - 4)
            expanded = _expand_tabs(text)
            wrapped = _wrap_by_width(expanded, max_input)
            base = max(_MIN_INPUT_ROWS, len(wrapped))
        return base + popup_height

    def bottom_lines(self, text: str, term_width: int, popup_height: int) -> int:
        """当前底部栏总行数（分隔线 + 状态行 + 输入行）。"""
        return 2 + self.compute_input_rows(text, term_width, popup_height)

    def get_cursor_info(
        self, text: str, cursor_pos: int, term_height: int, term_width: int,
    ) -> tuple[str, int, int, int]:
        """获取光标定位所需数据快照。

        使用 _last_rendered_text 而非当前 text 作为定位基准，
        防止 force_redraw 和 set_input_state 的竞态导致光标偏移。
        """
        render_text = self._last_rendered_text if self._last_rendered_text else text
        clamp_pos = min(cursor_pos, len(render_text))
        return (render_text, clamp_pos, term_height, term_width)

    def compute_cursor_position(
        self,
        text: str,
        cursor_pos: int,
        h: int,
        w: int,
        popup_height: int,
    ) -> tuple[int, int]:
        """计算光标在底部栏中的终端行号和列号（公开 API）。

        Args:
            text: 当前输入文本
            cursor_pos: 光标在文本中的偏移位置
            h: 终端高度
            w: 终端宽度
            popup_height: 补全弹窗高度

        Returns:
            (r_cursor, cursor_col) — 光标所在行号（1-based）和列号（1-based）
        """
        max_input = max(1, w - 4)
        vis_row, vis_col = self._cursor_visual_pos_from_cache(text, cursor_pos, max_input)
        total_bottom = max(5, 2 + self.compute_input_rows(text, w, popup_height))
        r_cursor = max(1, h - total_bottom + 3 + popup_height + vis_row)
        cursor_col = min(3 + vis_col, w)
        return (r_cursor, cursor_col)

    def _cursor_visual_pos_from_cache(
        self, text: str, cursor_pos: int, max_width: int,
    ) -> tuple[int, int]:
        """从缓存的拆行结果计算光标视觉位置（0-based）。

        复用 _cached_wrapped_lines，缓存失效时自动计算。
        """
        if (self._cached_wrapped_for != text
                or self._cached_wrapped_width != max_width
                or self._cached_wrapped_lines is None):
            expanded = _expand_tabs(text)
            self._cached_wrapped_lines = _wrap_by_width(expanded, max_width)
            self._cached_wrapped_for = text
            self._cached_wrapped_width = max_width
        abs_cursor = len(text) if cursor_pos < 0 else cursor_pos
        from ._bottom_cursor import _tab_pos_to_expanded
        expanded_pos = _tab_pos_to_expanded(text, abs_cursor)
        if expanded_pos < 0:
            expanded_pos = sum(len(s) for s in self._cached_wrapped_lines)
        newlines_before = text[:abs_cursor].count('\n')
        adjusted_pos = expanded_pos - newlines_before
        wrapped = self._cached_wrapped_lines
        cum = 0
        for i, seg in enumerate(wrapped):
            seg_len = len(seg)
            if adjusted_pos <= cum + seg_len:
                if adjusted_pos == cum + seg_len and i + 1 < len(wrapped):
                    return (i + 1, 0)
                prefix = seg[:adjusted_pos - cum]
                col = wcswidth(prefix)
                return (i, col)
            cum += seg_len
        last_idx = len(wrapped) - 1 if wrapped else 0
        last_col = wcswidth(wrapped[-1]) if wrapped else 0
        return (last_idx, last_col)

    def draw_input_lines(
        self,
        completion: "_CompletionPopup",
        text: str,
        status_active: bool,
        r_start: int,
        term_width: int,
        cursor_tracker,
    ) -> None:
        """绘制输入行（需持有 output_lock），超长文本自动拆行。

        Args:
            completion: 补全弹窗实例
            text: 输入文本
            status_active: 是否在流式输出中
            r_start: 第一行输入区的行号
            term_width: 当前终端宽度
            cursor_tracker: 光标坐标追踪器
        """
        out = sys.__stdout__
        max_input = max(1, term_width - 4)
        expanded = _expand_tabs(text)
        wrapped = _wrap_by_width(expanded, max_input)
        self._cached_wrapped_for = text
        self._cached_wrapped_width = max_input
        self._cached_wrapped_lines = wrapped
        base_rows = max(_MIN_INPUT_ROWS, len(wrapped))
        self._cached_input_rows = base_rows + completion.height
        self._last_rendered_text = text

        # 补全弹窗
        completion.render(out, r_start, term_width)
        popup_height = completion.height

        # 输入文本行
        text_start = r_start + popup_height
        for i, segment in enumerate(wrapped):
            r = text_start + i
            if i == 0:
                if text:
                    out.write(blessed_move_clear(r)
                              + f"{_COLOR_DEEP_CYAN}>{_COLOR_RESET}"
                              f" {segment}")
                else:
                    if status_active:
                        ph = _PLACEHOLDER_STREAMING
                        out.write(blessed_move_clear(r)
                                  + f"{_COLOR_DEEP_CYAN}>{_COLOR_RESET}"
                                  f" {_COLOR_DIM}{ph}{_COLOR_RESET}")
                    else:
                        ph = _PLACEHOLDER_COMPACT if completion.is_visible else _PLACEHOLDER_TEXT
                        out.write(blessed_move_clear(r)
                                  + f"{_COLOR_DEEP_CYAN}>{_COLOR_RESET}"
                                  f" {_COLOR_DIM}{ph}{_COLOR_RESET}")
            else:
                out.write(blessed_move_clear(r)
                          + f"{_COLOR_DIM}\u00b7{_COLOR_RESET} {segment}")
            cursor_tracker.set(r, 3)
        # 填充剩余空白行
        for r in range(text_start + len(wrapped), text_start + 3):
            out.write(blessed_move_clear(r) + "  ")
            cursor_tracker.set(r, 1)
