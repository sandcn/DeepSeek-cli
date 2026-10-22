"""输入区「满宽行 → 光标落下一行」布局/光标回归测试（2026-10-22 用户需求）。

需求：输入字符填满一行（到达行尾）时，光标（插入点）显示在**下一行行首**，
而不是停在当前行最后一列压住最后一个字符。

实现：
  - ``_compute_input_layout``：最后一段恰好填满一行时追加一个空段
    （输入区多显示一行承载光标）；
  - ``_cursor_visual_from_layout``：段边界归属下一段（严格 ``<``），
    满宽行末的光标映射为下一行第 0 列。

覆盖：单行满宽、未满宽不变、中间软换行边界、多逻辑行、宽字符（CJK）、
制表符展开后满宽、``_compute_cursor_visual_pos`` 便捷入口一致性。
"""

from __future__ import annotations

from src.tui._input_layout import (
    _compute_cursor_visual_pos,
    _compute_input_layout,
    _cursor_visual_from_layout,
)


def _cursor(text: str, cursor_pos: int, max_input: int) -> tuple[int, int]:
    _, wrapped = _compute_input_layout(text, max_input)
    return _cursor_visual_from_layout(text, cursor_pos, wrapped)


def test_full_line_appends_empty_segment() -> None:
    rows, wrapped = _compute_input_layout("x" * 5, 5)
    assert rows == 2
    assert wrapped == [["xxxxx", ""]]


def test_cursor_at_full_line_end_moves_to_next_line() -> None:
    assert _cursor("x" * 5, 5, 5) == (1, 0)


def test_cursor_before_line_end_stays_on_same_line() -> None:
    assert _cursor("x" * 4, 4, 5) == (0, 4)
    # 光标在满宽行中间（不是边界）仍在本行。
    assert _cursor("x" * 5, 3, 5) == (0, 3)


def test_soft_wrap_boundary_middle_text_goes_next_line() -> None:
    text = "x" * 10
    rows, wrapped = _compute_input_layout(text, 5)
    assert rows == 3
    assert wrapped == [["xxxxx", "xxxxx", ""]]
    # 行内边界（第 5 个字符之后）与文末边界都归属下一行行首。
    assert _cursor_visual_from_layout(text, 5, wrapped) == (1, 0)
    assert _cursor_visual_from_layout(text, 10, wrapped) == (2, 0)


def test_no_extra_row_when_last_line_not_full() -> None:
    rows, wrapped = _compute_input_layout("abc", 5)
    assert rows == 1
    assert wrapped == [["abc"]]
    assert _cursor("abc", 3, 5) == (0, 3)


def test_multiline_last_segment_full_appends_only_at_end() -> None:
    text = "ab\nxxxxx"
    rows, wrapped = _compute_input_layout(text, 5)
    assert rows == 3
    assert wrapped == [["ab"], ["xxxxx", ""]]
    # 第二逻辑行末（满宽）→ 下一行行首。
    assert _cursor_visual_from_layout(text, len(text), wrapped) == (2, 0)


def test_wide_chars_fill_exactly_appends_row() -> None:
    text = "中" * 3  # 显示宽度 6
    rows, wrapped = _compute_input_layout(text, 6)
    assert rows == 2
    assert wrapped == [["中中中", ""]]
    assert _cursor(text, len(text), 6) == (1, 0)


def test_tab_expansion_full_width_appends_row() -> None:
    # "\t" 展开为 4 列（_TAB_WIDTH），恰好填满 max_input=4。
    text = "\t"
    rows, wrapped = _compute_input_layout(text, 4)
    assert rows == 2
    assert wrapped == [["    ", ""]]
    assert _cursor(text, len(text), 4) == (1, 0)


def test_compute_cursor_visual_pos_matches_layout_helper() -> None:
    text = "x" * 5
    assert _compute_cursor_visual_pos(text, 5, 5) == _cursor(text, 5, 5)
    assert _compute_cursor_visual_pos("abc", 3, 5) == _cursor("abc", 3, 5)
