"""Tests for RenderBuffer StyledCell 富文本支持（C2）。

覆盖：
  - StyledCell 单独渲染（有/无样式）
  - write() 带 style 参数写入
  - 相邻同样式字符自动合并 ANSI 包裹
  - 不同样式切换
  - 样式+无样式混合行
  - 跨行样式隔离（不跨行合并）
  - merge() 带样式源缓冲区
  - render_raw() 带样式
  - StyledCell 属性与相等性
  - 向后兼容：write() 无 style 参数行为不变
"""

from __future__ import annotations

import unittest


class TestStyledCell(unittest.TestCase):
    """Test StyledCell basic operations."""

    def test_no_style_returns_char(self):
        """StyledCell 无 style 时直接返回字符（零开销）。"""
        from src.tui.render_buffer import StyledCell
        cell = StyledCell("A")
        self.assertEqual(cell.render(), "A")

    def test_with_style_returns_ansi(self):
        """StyledCell 有 style 时返回 ANSI 包裹字符。"""
        from src.tui.render_buffer import StyledCell
        from src.tui.core.style import Style
        cell = StyledCell("A", Style(fg=45))
        expected = "\033[38;5;45mA\033[0m"
        self.assertEqual(cell.render(), expected)

    def test_has_style_true(self):
        """StyledCell.has_style 对有 style 返回 True。"""
        from src.tui.render_buffer import StyledCell
        from src.tui.core.style import Style
        cell = StyledCell("A", Style(fg=45))
        self.assertTrue(cell.has_style)

    def test_has_style_false(self):
        """StyledCell.has_style 对无 style 返回 False。"""
        from src.tui.render_buffer import StyledCell
        cell = StyledCell("A")
        self.assertFalse(cell.has_style)

    def test_different_style_property(self):
        """StyledCell 不同 char/style 时不相等，相等时相等。"""
        from src.tui.render_buffer import StyledCell
        from src.tui.core.style import Style
        s = Style(fg=45)
        cell_a = StyledCell("A", s)
        cell_a2 = StyledCell("A", s)
        cell_b = StyledCell("B", s)
        cell_a_none = StyledCell("A")
        self.assertEqual(cell_a, cell_a2)
        self.assertNotEqual(cell_a, cell_b)
        self.assertNotEqual(cell_a, cell_a_none)

    def test_repr(self):
        """StyledCell.__repr__ 包含 char 和 style 信息。"""
        from src.tui.render_buffer import StyledCell
        from src.tui.core.style import Style
        cell = StyledCell("A", Style(fg=45))
        r = repr(cell)
        self.assertIn("StyledCell", r)
        self.assertIn("A", r)
        self.assertIn("45", r)


class TestRenderBufferStyleWrite(unittest.TestCase):
    """Test RenderBuffer write() with style parameter."""

    def test_write_with_style(self):
        """write() 带 style 参数正确存储 StyledCell。"""
        from src.tui.render_buffer import RenderBuffer
        from src.tui.core.style import Style
        buf = RenderBuffer(10, 3)
        buf.write(0, 0, "Hello", style=Style(fg=45))
        # 渲染结果应含 ANSI 包裹
        output = buf.render()
        self.assertIn("\033[38;5;45m", output)
        self.assertIn("Hello", output)

    def test_write_without_style_backward_compat(self):
        """write() 无 style 参数行为不变（向后兼容）。"""
        from src.tui.render_buffer import RenderBuffer
        buf = RenderBuffer(10, 3)
        buf.write(0, 0, "Hello")
        output = buf.render()
        self.assertEqual(output, "Hello")
        self.assertNotIn("\033", output)

    def test_write_char_with_style(self):
        """write_char() 带 style 参数。"""
        from src.tui.render_buffer import RenderBuffer
        from src.tui.core.style import Style
        buf = RenderBuffer(5, 3)
        buf.write_char(2, 1, "X", style=Style(fg=45))
        output = buf.render()
        self.assertIn("\033[38;5;45mX\033[0m", output)

    def test_write_mixed_style(self):
        """同一行中混合样式与无样式写入。"""
        from src.tui.render_buffer import RenderBuffer
        from src.tui.core.style import Style
        buf = RenderBuffer(20, 3)
        buf.write(0, 0, "Hello ", style=Style(fg=45))
        buf.write(6, 0, "World")
        output = buf.render()
        # Hello 带样式，World 无样式
        self.assertIn("\033[38;5;45mHello \033[0mWorld", output)


class TestRenderBufferStyleMerge(unittest.TestCase):
    """Test RenderBuffer 相邻同样式字符合并。"""

    def test_adjacent_same_style_merged(self):
        """相邻同样式字符合并为单个 ANSI 包裹。"""
        from src.tui.render_buffer import RenderBuffer
        from src.tui.core.style import Style
        buf = RenderBuffer(20, 3)
        buf.write(0, 0, "AB", style=Style(fg=45))
        buf.write(2, 0, "CD", style=Style(fg=45))
        output = buf.render()
        # 应只有一次 ANSI 开启/关闭
        count_open = output.count("\033[38;5;45m")
        count_close = output.count("\033[0m")
        self.assertEqual(count_open, 1, "相邻同样式应合并为单个 ANSI 开启")
        self.assertEqual(count_close, 1, "相邻同样式应合并为单个 ANSI 关闭")
        self.assertEqual(output, "\033[38;5;45mABCD\033[0m")

    def test_different_styles_not_merged(self):
        """不同样式字符不合并。"""
        from src.tui.render_buffer import RenderBuffer
        from src.tui.core.style import Style
        buf = RenderBuffer(20, 3)
        buf.write(0, 0, "Red", style=Style(fg=1))
        buf.write(3, 0, "Green", style=Style(fg=2))
        output = buf.render()
        # 两种不同样式，应有两次独立的 ANSI 包裹
        self.assertIn("\033[38;5;1mRed\033[0m", output)
        self.assertIn("\033[38;5;2mGreen\033[0m", output)

    def test_style_to_none_transition(self):
        """样式→无样式→样式的正确切换。"""
        from src.tui.render_buffer import RenderBuffer
        from src.tui.core.style import Style
        buf = RenderBuffer(20, 3)
        buf.write(0, 0, "S", style=Style(fg=45))
        buf.write(1, 0, "p")
        buf.write(2, 0, "S", style=Style(fg=45))
        output = buf.render()
        # S(样式) p(无样式) S(样式) — 三段独立
        expected = "\033[38;5;45mS\033[0mp\033[38;5;45mS\033[0m"
        self.assertEqual(output, expected)

    def test_cross_line_no_merge(self):
        """跨行样式不合并（每行独立渲染）。"""
        from src.tui.render_buffer import RenderBuffer
        from src.tui.core.style import Style
        buf = RenderBuffer(10, 3)
        buf.write(0, 0, "AB", style=Style(fg=45))
        buf.write(0, 1, "CD", style=Style(fg=45))
        output = buf.render()
        lines = output.split("\n")
        self.assertEqual(len(lines), 2)
        # 每行各自有独立的 ANSI 包裹
        self.assertEqual(lines[0], "\033[38;5;45mAB\033[0m")
        self.assertEqual(lines[1], "\033[38;5;45mCD\033[0m")


class TestRenderBufferMergeStyled(unittest.TestCase):
    """Test RenderBuffer merge() with styled source."""

    def test_merge_styled_source(self):
        """merge() 从带样式的源缓冲区叠加。"""
        from src.tui.render_buffer import RenderBuffer
        from src.tui.core.style import Style
        buf = RenderBuffer(10, 5)
        buf.write(0, 0, "Hello")

        overlay = RenderBuffer(5, 3)
        overlay.write(0, 0, "World", style=Style(fg=45))
        buf.merge(overlay, x=2, y=1)

        output = buf.render()
        self.assertIn("Hello", output)
        self.assertIn("\033[38;5;45mWorld\033[0m", output)

    def test_merge_transparent_with_style(self):
        """merge() transparent_char 过滤正确，保留样式。"""
        from src.tui.render_buffer import RenderBuffer
        from src.tui.core.style import Style
        buf = RenderBuffer(10, 3)
        buf.write(1, 0, "X")

        overlay = RenderBuffer(3, 1)
        overlay.write(0, 0, " A ", style=Style(fg=45))
        buf.merge(overlay, x=0, y=0)

        output = buf.render()
        # 'A' should replace ' ' at (1,0), styled
        self.assertIn("\033[38;5;45mA\033[0m", output)
        # 'X' should be gone (overwritten by 'A')
        self.assertNotIn("X", output)


class TestRenderBufferStyleRender(unittest.TestCase):
    """Test RenderBuffer render methods with styles."""

    def test_render_raw_with_style(self):
        """render_raw() 保留行尾空格且正确输出样式。"""
        from src.tui.render_buffer import RenderBuffer
        from src.tui.core.style import Style
        buf = RenderBuffer(10, 2)
        buf.write(0, 0, "Hello", style=Style(fg=45))
        raw = buf.render_raw()
        # 行应有 10 个字符（"Hello" + 5 空格），且包含 ANSI
        first_line = raw.split("\n")[0]
        self.assertEqual(len(first_line), len("\033[38;5;45mHello\033[0m") + 5)
        self.assertIn("\033[38;5;45mHello\033[0m", raw)

    def test_render_styled_empty_buffer(self):
        """空缓冲区 render() 返回空字符串。"""
        from src.tui.render_buffer import RenderBuffer
        buf = RenderBuffer(0, 0)
        self.assertEqual(buf.render(), "")

    def test_render_styled_trailing_whitespace_trimmed(self):
        """render() 自动去除行尾空白（含样式字符后）。"""
        from src.tui.render_buffer import RenderBuffer
        from src.tui.core.style import Style
        buf = RenderBuffer(10, 3)
        buf.write(0, 0, "Hi", style=Style(fg=45))
        output = buf.render()
        # 末尾没有多余空格
        self.assertEqual(output, "\033[38;5;45mHi\033[0m")
        self.assertFalse(output.endswith(" "))


class TestRenderBufferStyleGrid(unittest.TestCase):
    """Test internal grid structure with StyledCell."""

    def test_grid_contains_styled_cells(self):
        """_grid 存储 StyledCell 对象。"""
        from src.tui.render_buffer import RenderBuffer, StyledCell
        buf = RenderBuffer(5, 3)
        self.assertIsInstance(buf._grid[0][0], StyledCell)

    def test_write_creates_styled_cell(self):
        """write() 写入后网格含正确 StyledCell。"""
        from src.tui.render_buffer import RenderBuffer, StyledCell
        from src.tui.core.style import Style
        buf = RenderBuffer(5, 3)
        buf.write(1, 1, "X", style=Style(fg=45))
        cell = buf._grid[1][1]
        self.assertIsInstance(cell, StyledCell)
        self.assertEqual(cell.char, "X")
        self.assertIsNotNone(cell.style)

    def test_write_without_style_stores_none(self):
        """write() 无 style 时 StyledCell.style 为 None。"""
        from src.tui.render_buffer import RenderBuffer, StyledCell
        buf = RenderBuffer(5, 3)
        buf.write(0, 0, "A")
        cell = buf._grid[0][0]
        self.assertIsNone(cell.style)


class TestRenderBufferStyleEdgeCases(unittest.TestCase):
    """Test edge cases for styled render buffer."""

    def test_write_out_of_bounds_with_style(self):
        """越界写入带 style 不抛异常。"""
        from src.tui.render_buffer import RenderBuffer
        from src.tui.core.style import Style
        buf = RenderBuffer(10, 3)
        # 各种越界场景
        buf.write(-1, 0, "test", style=Style(fg=45))
        buf.write(0, 100, "test", style=Style(fg=45))
        buf.write(100, 0, "test", style=Style(fg=45))
        # 不抛异常即为通过

    def test_empty_text_with_style(self):
        """空文本带 style 写入不操作。"""
        from src.tui.render_buffer import RenderBuffer
        from src.tui.core.style import Style
        buf = RenderBuffer(10, 3)
        buf.write(0, 0, "", style=Style(fg=45))
        # 空写入后 render 应为空
        output = buf.render()
        self.assertEqual(output, "")

    def test_single_cell_style(self):
        """单一单元格带样式正确渲染。"""
        from src.tui.render_buffer import RenderBuffer
        from src.tui.core.style import Style
        buf = RenderBuffer(5, 3)
        buf.write_char(2, 1, "X", style=Style(fg=45))
        output = buf.render()
        # 行 0 空，行 1 含 "  X"（2空格+X）
        self.assertIn("\033[38;5;45mX\033[0m", output)
        self.assertNotIn("\033[0m\033[38;5;45m", output)

    def test_clear_after_styled_write(self):
        """clear() 清空带样式内容。"""
        from src.tui.render_buffer import RenderBuffer
        from src.tui.core.style import Style
        buf = RenderBuffer(5, 3)
        buf.write(0, 0, "Hi", style=Style(fg=45))
        buf.clear()
        output = buf.render()
        self.assertEqual(output, "")

    def test_fill_after_style(self):
        """fill() 覆盖带样式区域。"""
        from src.tui.render_buffer import RenderBuffer
        from src.tui.core.style import Style
        buf = RenderBuffer(5, 3)
        buf.write(0, 0, "Hi", style=Style(fg=45))
        buf.fill("-", 0, 0, 2, 1)
        output = buf.render()
        self.assertEqual(output, "--")

    def test_hline_with_style_grid(self):
        """hline() 创建正确 StyledCell 网格。"""
        from src.tui.render_buffer import RenderBuffer, StyledCell
        buf = RenderBuffer(5, 3)
        buf.hline(1, "-")
        for col in range(5):
            cell = buf._grid[1][col]
            self.assertIsInstance(cell, StyledCell)
            self.assertEqual(cell.char, "-")
            self.assertIsNone(cell.style)


if __name__ == "__main__":
    unittest.main()
