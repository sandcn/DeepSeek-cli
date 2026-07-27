"""CursorPositioner 单元测试。

测试范围：
  - 单行文本 + 光标在末尾 → 正确行/列
  - 多行文本 + 光标在中间行 → 视觉行计算正确
  - 含制表符文本 → 正确展开
  - 空文本 → 默认位置
  - 终端高度极小时的钳位
"""

from __future__ import annotations

import pytest
from unittest import mock


class TestCursorPositioner:
    """CursorPositioner 单元测试。"""

    @pytest.fixture
    def mock_width_cache(self):
        """Mock TerminalWidthCache 返回固定宽度 80、高度 24。"""
        cache = mock.MagicMock()
        cache.get_width.return_value = 80
        cache.get_height.return_value = 24
        return cache

    def _make_positioner(self, mock_width_cache):
        """创建 CursorPositioner 实例。"""
        from src.tui.input._cursor import CursorPositioner
        return CursorPositioner(width_cache=mock_width_cache)

    def test_single_line_cursor_at_end(self, mock_width_cache):
        """单行文本 + 光标在末尾 → 正确行/列。"""
        pos = self._make_positioner(mock_width_cache)

        r_cursor, cursor_col, vis_row, vis_col = pos.compute(
            text="hello world",
            cursor_pos=11,
            bottom_lines=6,
            subagent_lines=0,
            completion_height=0,
        )

        # vis_row=0, vis_col=11 (visual width of "hello world")
        # r_cursor = 24 - 6 + 4 + 0 + 0 + 0 = 22
        assert vis_row == 0
        assert r_cursor == 22
        # cursor_col = min(3 + 11, 80) = 14
        assert cursor_col == 14

    def test_empty_text_default_position(self, mock_width_cache):
        """空文本 → 默认位置 (vis_row=0, vis_col=0)。"""
        pos = self._make_positioner(mock_width_cache)

        r_cursor, cursor_col, vis_row, vis_col = pos.compute(
            text="",
            cursor_pos=0,
            bottom_lines=4,
            subagent_lines=0,
            completion_height=0,
        )

        assert vis_row == 0
        assert vis_col == 0
        assert r_cursor == 24  # 24 - 4 + 4 + 0 + 0 + 0

    def test_multiline_text_cursor_in_middle(self, mock_width_cache):
        """多行文本 + 光标在中间行 → 视觉行计算正确。"""
        pos = self._make_positioner(mock_width_cache)

        text = "line one\nline two\nline three"
        # 光标在 "line two" 的 "line " 之后（"line one\nline "）
        # "line one\n" = 9 chars, "line " = 5 chars, cursor_pos=14
        cursor_pos = 14

        r_cursor, cursor_col, vis_row, vis_col = pos.compute(
            text=text,
            cursor_pos=cursor_pos,
            bottom_lines=6,
            subagent_lines=0,
            completion_height=0,
        )

        # vis_row should be 1 (second visual line, 0-based)
        assert vis_row == 1
        # vis_col should be wcswidth("line ") = 5
        assert vis_col == 5
        # r_cursor = 24 - 6 + 4 + 0 + 0 + 1 = 23
        assert r_cursor == 23

    def test_tab_expansion(self, mock_width_cache):
        """含制表符文本 → 视觉列正确展开。"""
        pos = self._make_positioner(mock_width_cache)

        # "a\tb": tab expands to 3 spaces (tab at col 1, next tab stop at 4)
        # expanded text: "a   b" (4 chars, width 4)
        r_cursor, cursor_col, vis_row, vis_col = pos.compute(
            text="a\tb",
            cursor_pos=2,  # after the tab
            bottom_lines=6,
            subagent_lines=0,
            completion_height=0,
        )

        # vis_col should be 4 (a=1 + tab→3 spaces = 4)
        assert vis_col == 4

    def test_completion_height_offset(self, mock_width_cache):
        """补全弹窗高度影响 r_cursor 计算（高度足够时不钳位）。"""
        mock_width_cache.get_height.return_value = 50  # 足够大避免钳位
        pos = self._make_positioner(mock_width_cache)

        # 无补全弹窗
        r1, _, _, _ = pos.compute(
            text="hi", cursor_pos=2,
            bottom_lines=6, subagent_lines=0, completion_height=0,
        )
        # r1 = 50 - 6 + 4 + 0 + 0 + 0 = 48

        # 补全弹窗 3 行
        r2, _, _, _ = pos.compute(
            text="hi", cursor_pos=2,
            bottom_lines=6, subagent_lines=0, completion_height=3,
        )
        # r2 = 50 - 6 + 4 + 0 + 3 + 0 = 51 → clamp to 50

        # r2 应该比 r1 大（向下偏移补全弹窗高度）
        assert r2 > r1

    def test_subagent_lines_offset(self, mock_width_cache):
        """subagent 面板行影响 r_cursor 计算。"""
        pos = self._make_positioner(mock_width_cache)

        r1, _, _, _ = pos.compute(
            text="hi", cursor_pos=2,
            bottom_lines=6, subagent_lines=0, completion_height=0,
        )

        r2, _, _, _ = pos.compute(
            text="hi", cursor_pos=2,
            bottom_lines=6, subagent_lines=2, completion_height=0,
        )

        # r2 应该比 r1 大 2（向下偏移 subagent 面板行数）
        assert r2 == r1 + 2

    def test_clamp_to_terminal_height(self, mock_width_cache):
        """r_cursor 被钳位到 [1, height]。"""
        mock_width_cache.get_height.return_value = 5
        pos = self._make_positioner(mock_width_cache)

        # 如果计算结果超出终端高度，应被钳位
        r_cursor, _, _, _ = pos.compute(
            text="line 1\nline 2\nline 3\nline 4",
            cursor_pos=25,
            bottom_lines=3,
            subagent_lines=0,
            completion_height=0,
        )

        assert 1 <= r_cursor <= 5

    def test_cursor_col_clamp_to_width(self, mock_width_cache):
        """cursor_col 被钳位到 [1, width]。"""
        mock_width_cache.get_width.return_value = 20
        pos = self._make_positioner(mock_width_cache)

        _, cursor_col, _, _ = pos.compute(
            text="a very long line that exceeds terminal width",
            cursor_pos=50,
            bottom_lines=6,
            subagent_lines=0,
            completion_height=0,
        )

        assert 1 <= cursor_col <= 20
