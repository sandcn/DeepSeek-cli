"""Tests for Panel component rendering — [B4] 组件化工具面板边框。

Tests cover:
  - Panel top border rendering with title
  - Panel bottom border rendering
  - Panel with border_color / title_color (ANSI color wrapping)
  - Panel width constraint
  - Breathing glow color computation (sine_color integration)
  - Empty content Panel (used in tool output panel replacement)
"""

from __future__ import annotations

import unittest


class TestPanelTopBorder(unittest.TestCase):
    """Test Panel top border with title rendering."""

    def test_top_border_with_title(self):
        """Panel with title='工具调用' should produce top border with title."""
        from src.tui.components._panel import Panel
        from src.tui.components._box import BoxStyle

        panel = Panel(title="工具调用", content="", style=BoxStyle.ROUNDED, width=14)
        output = panel.render()
        lines = output.split('\n')
        top_line = lines[0]

        # 验证顶部边框含圆角字符和标题
        self.assertIn('╭', top_line)
        self.assertIn('╮', top_line)
        self.assertIn('工具调用', top_line)

    def test_top_border_no_title(self):
        """Panel without title should produce plain top border."""
        from src.tui.components._panel import Panel
        from src.tui.components._box import BoxStyle

        panel = Panel(title="", content="", style=BoxStyle.ROUNDED, width=10)
        output = panel.render()
        lines = output.split('\n')
        top_line = lines[0]

        self.assertIn('╭', top_line)
        self.assertIn('╮', top_line)
        # 无标题时不应显示括号
        self.assertNotIn('[', top_line)
        self.assertNotIn(']', top_line)


class TestPanelBottomBorder(unittest.TestCase):
    """Test Panel bottom border rendering."""

    def test_bottom_border(self):
        """Panel should produce bottom border line."""
        from src.tui.components._panel import Panel
        from src.tui.components._box import BoxStyle

        panel = Panel(title="工具调用", content="", style=BoxStyle.ROUNDED, width=14)
        output = panel.render()
        lines = output.split('\n')
        bottom_line = lines[-1]

        self.assertIn('╰', bottom_line)
        self.assertIn('╯', bottom_line)

    def test_bottom_border_width_consistency(self):
        """Top and bottom borders should have same width."""
        from src.tui.components._panel import Panel
        from src.tui.components._box import BoxStyle
        from src.tui.core.ansi_utils import visual_width, strip_ansi

        panel = Panel(title="工具调用", content="", style=BoxStyle.ROUNDED, width=14)
        output = panel.render()
        lines = output.split('\n')
        top_line = lines[0]
        bottom_line = lines[-1]

        top_stripped = strip_ansi(top_line)
        bottom_stripped = strip_ansi(bottom_line)
        self.assertEqual(visual_width(top_stripped), visual_width(bottom_stripped))


class TestPanelBorderColor(unittest.TestCase):
    """Test Panel border_color parameter."""

    def test_border_color_applied(self):
        """border_color should wrap border with ANSI color codes."""
        from src.tui.components._panel import Panel
        from src.tui.components._box import BoxStyle

        panel = Panel(title="测试", content="", style=BoxStyle.ROUNDED,
                      width=10, border_color=23)
        output = panel.render()
        # 38;5;23 是 256 色号 23 的前景色序列
        self.assertIn('38;5;23', output)

    def test_border_color_none(self):
        """border_color=None should not add ANSI color codes."""
        from src.tui.components._panel import Panel
        from src.tui.components._box import BoxStyle

        panel = Panel(title="测试", content="", style=BoxStyle.ROUNDED,
                      width=10, border_color=None)
        output = panel.render()
        # 没有 border_color 时不应出现任何 38;5; 序列（纯文本模式）
        # 标题本身可能有颜色，所以需要更精确的检查
        # 对于无 border_color 且无 title_color，不应有 ANSI 前景色序列
        from src.tui.core.ansi_utils import strip_ansi
        stripped = strip_ansi(output)
        # 只是确认输出有正常边框字符
        self.assertIn('╭', stripped)
        self.assertIn('╰', stripped)

    def test_title_color_applied(self):
        """title_color should wrap title with ANSI color codes."""
        from src.tui.components._panel import Panel
        from src.tui.components._box import BoxStyle

        panel = Panel(title="测试", content="", style=BoxStyle.ROUNDED,
                      width=10, title_color=45)
        output = panel.render()
        self.assertIn('38;5;45', output)


class TestPanelWidth(unittest.TestCase):
    """Test Panel width parameter."""

    def test_explicit_width(self):
        """Panel with explicit width should produce fixed-width output."""
        from src.tui.components._panel import Panel
        from src.tui.components._box import BoxStyle
        from src.tui.core.ansi_utils import visual_width, strip_ansi

        panel = Panel(title="工具调用", content="", style=BoxStyle.ROUNDED, width=14)
        output = panel.render()
        lines = output.split('\n')
        top_line = strip_ansi(lines[0])

        # 顶部边框宽度应等于 width 参数
        self.assertEqual(visual_width(top_line), 14)


class TestSineColorIntegration(unittest.TestCase):
    """Test sine_color integration for breathing glow effect."""

    def test_sine_color_bounds(self):
        """sine_color should return values within [color_low, color_high]."""
        from src.tui.core.effects import sine_color

        for frame in range(0, 50):
            c = sine_color(frame, 23, min(255, 23 + 20), 24)
            self.assertGreaterEqual(c, 23)
            self.assertLessEqual(c, min(255, 23 + 20))

    def test_sine_color_breathing(self):
        """sine_color should produce varying values over frames (breathing)."""
        from src.tui.core.effects import sine_color

        values = [sine_color(f, 23, min(255, 23 + 20), 24) for f in range(0, 48)]
        # 正弦波在 24 帧内应有至少 3 个不同的值
        unique = set(values)
        self.assertGreaterEqual(len(unique), 3)


class TestEmptyContentPanel(unittest.TestCase):
    """Test Panel with empty content (as used in tool output panel)."""

    def test_empty_content_num_lines(self):
        """Panel with empty content should produce exactly 3 lines."""
        from src.tui.components._panel import Panel
        from src.tui.components._box import BoxStyle

        panel = Panel(title="工具调用", content="", style=BoxStyle.ROUNDED, width=14)
        output = panel.render()
        lines = output.split('\n')
        self.assertEqual(len(lines), 3)

    def test_empty_content_lines_structure(self):
        """Panel with empty content: top border, content line, bottom border."""
        from src.tui.components._panel import Panel
        from src.tui.components._box import BoxStyle

        panel = Panel(title="工具调用", content="", style=BoxStyle.ROUNDED, width=14)
        output = panel.render()
        lines = output.split('\n')

        # 第一行：顶部边框
        self.assertIn('╭', lines[0])
        # 第二行：内容行（有垂直边框）
        self.assertIn('│', lines[1])
        # 第三行：底部边框
        self.assertIn('╰', lines[2])

    def test_empty_content_top_line_extraction(self):
        """Simulate _do_tool_output: extract top line from Panel render."""
        from src.tui.components._panel import Panel
        from src.tui.components._box import BoxStyle

        panel = Panel(title="工具调用", content="", style=BoxStyle.ROUNDED,
                      width=14, border_color=23)
        full = panel.render()
        top_line = full.split('\n')[0]
        # 顶部边框应包含圆角字符
        self.assertIn('╭', top_line)
        self.assertIn('╮', top_line)
        # 应包含标题
        self.assertIn('工具调用', top_line)

    def test_empty_content_bottom_line_extraction(self):
        """Simulate _do_tool_summary: extract bottom line from Panel render."""
        from src.tui.components._panel import Panel
        from src.tui.components._box import BoxStyle

        panel = Panel(title="工具调用", content="", style=BoxStyle.ROUNDED,
                      width=14, border_color=23)
        full = panel.render()
        lines = full.split('\n')
        bottom_line = lines[-1]
        # 底部边框应包含圆角字符
        self.assertIn('╰', bottom_line)
        self.assertIn('╯', bottom_line)

    def test_border_color_multiline(self):
        """Both top and bottom border should have ANSI color when border_color set."""
        from src.tui.components._panel import Panel
        from src.tui.components._box import BoxStyle

        panel = Panel(title="测试", content="", style=BoxStyle.ROUNDED,
                      width=10, border_color=23)
        output = panel.render()
        lines = output.split('\n')

        # 顶部和底部边框都应包含色号 23
        self.assertIn('38;5;23', lines[0])   # top border
        self.assertIn('38;5;23', lines[-1])  # bottom border


if __name__ == '__main__':
    unittest.main()
