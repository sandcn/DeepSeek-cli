"""单元测试 — chat_ui measureElement 工具函数。"""

from __future__ import annotations

import pytest

from rich.text import Text as RichText

from src.chat_ui._measure import measureElement
from src.chat_ui._components import TuiComponent


# ═══════════════════════════════════════════════════════════
# 测试辅助组件
# ═══════════════════════════════════════════════════════════

class SimpleComponent(TuiComponent):
    """单行纯文本组件。"""

    def __init__(self, text: str = "hello"):
        super().__init__()
        self.text = text

    def render(self) -> str:
        return self.text


class MultiLineComponent(TuiComponent):
    """多行纯文本组件。"""

    def __init__(self, lines: int = 3):
        super().__init__()
        self.lines = lines

    def render(self) -> str:
        return "\n".join(f"line {i}" for i in range(self.lines))


class NoneComponent(TuiComponent):
    """render() 返回 None 的组件。"""

    def render(self):
        return None


class RichTextComponent(TuiComponent):
    """返回 Rich Text 对象的组件。"""

    def render(self) -> RichText:
        return RichText("hello world")


class AnsiComponent(TuiComponent):
    """返回含 ANSI 转义序列字符串的组件。"""

    def render(self) -> str:
        return "\x1b[31mhello\x1b[0m \x1b[32mworld\x1b[0m"


class EmptyComponent(TuiComponent):
    """返回空字符串的组件。"""

    def render(self) -> str:
        return ""


# ═══════════════════════════════════════════════════════════
# 测试用例
# ═══════════════════════════════════════════════════════════

class TestMeasureSingleLine:
    """单行组件测量。"""

    def test_measure_single_line(self):
        """单行组件返回 (1, width)。"""
        comp = SimpleComponent("hello")
        rows, cols = measureElement(comp)
        assert rows == 1, f"预期 1 行, 实际 {rows}"
        assert cols == 5, f"预期列宽 5, 实际 {cols}"

    def test_measure_single_line_short(self):
        """较短单行文本测量正确。"""
        comp = SimpleComponent("ok")
        rows, cols = measureElement(comp)
        assert rows == 1
        assert cols == 2

    def test_measure_single_line_long(self):
        """长单行文本在默认 80 列终端不换行。"""
        comp = SimpleComponent("a" * 70)
        rows, cols = measureElement(comp, terminal_width=80)
        assert rows == 1
        assert cols == 70


class TestMeasureMultiLine:
    """多行组件测量。"""

    def test_measure_multi_line(self):
        """多行组件返回 (N, max_width)。"""
        comp = MultiLineComponent(lines=3)
        rows, cols = measureElement(comp)
        # "line 0", "line 1", "line 2" — 每行 6 字符
        assert rows == 3, f"预期 3 行, 实际 {rows}"
        assert cols == 6, f"预期列宽 6, 实际 {cols}"

    def test_measure_multi_line_varying_width(self):
        """多行组件中各行宽度不同，max_cols 取最大值。"""

        class VaryingWidth(TuiComponent):
            def render(self):
                return "short\nmuch longer line here\nok"

        comp = VaryingWidth()
        rows, cols = measureElement(comp)
        assert rows == 3
        assert cols == len("much longer line here")  # 21


class TestMeasureEmptyComponent:
    """空组件测量。"""

    def test_measure_empty_component(self):
        """空组件返回 (0, 0)。"""
        comp = EmptyComponent()
        rows, cols = measureElement(comp)
        assert rows == 0, f"预期 0 行, 实际 {rows}"
        assert cols == 0, f"预期列宽 0, 实际 {cols}"

    def test_measure_empty_string(self):
        """render() 返回空字符串 → (0, 0)。"""
        comp = SimpleComponent("")
        rows, cols = measureElement(comp)
        assert rows == 0
        assert cols == 0


class TestMeasureComponentReturnsNone:
    """render() 返回 None 的测量。"""

    def test_measure_component_returns_none(self):
        """render() 返回 None → (0, 0)。"""
        comp = NoneComponent()
        rows, cols = measureElement(comp)
        assert rows == 0, f"预期 0 行, 实际 {rows}"
        assert cols == 0, f"预期列宽 0, 实际 {cols}"


class TestMeasureRichText:
    """Rich Text 的 .plain 属性被正确使用。"""

    def test_measure_rich_text(self):
        """Rich Text 对象通过 .plain 提取纯文本后测量。"""
        comp = RichTextComponent()
        rows, cols = measureElement(comp)
        assert rows == 1
        assert cols == 11  # "hello world" = 11 字符


class TestMeasureAnsiStripped:
    """ANSI 转义序列被正确剥离。"""

    def test_measure_ansi_stripped(self):
        """含 ANSI 转义序列的字符串，剥离后测量纯文本尺寸。"""
        comp = AnsiComponent()
        rows, cols = measureElement(comp)
        # ANSI 剥离后 → "hello world" (11 字符)
        assert rows == 1
        assert cols == 11

    def test_measure_ansi_only(self):
        """纯 ANSI 序列（无可见文本）→ (0, 0)。"""

        class AnsiOnly(TuiComponent):
            def render(self):
                return "\x1b[31m\x1b[0m"

        comp = AnsiOnly()
        rows, cols = measureElement(comp)
        assert rows == 0
        assert cols == 0

    def test_measure_ansi_with_newlines(self):
        """ANSI 序列跨多行时正确剥离。"""

        class AnsiMultiLine(TuiComponent):
            def render(self):
                return "\x1b[31mline1\x1b[0m\n\x1b[32mline2\x1b[0m"

        comp = AnsiMultiLine()
        rows, cols = measureElement(comp)
        assert rows == 2  # "line1" + "line2"
        assert cols == 5  # max("line1", "line2") = 5


class TestMeasureCJKCharacters:
    """CJK 宽字符（中文/日文）占 2 列宽，在窄终端中正确换行。"""

    def test_measure_cjk_characters(self):
        """CJK 字符每个占 2 列宽。"""

        class CjkComponent(TuiComponent):
            def render(self):
                return "你好"

        comp = CjkComponent()
        rows, cols = measureElement(comp)
        assert rows == 1
        assert cols == 4  # 2 字符 × 2 列宽 = 4

    def test_measure_cjk_wrap_narrow(self):
        """CJK 字符在窄终端中正确换行。"""

        class CjkLong(TuiComponent):
            def render(self):
                return "你好世界"  # 4 个 CJK 字符 = 8 列宽

        comp = CjkLong()
        # terminal_width=6: 8 列宽 ÷ 6 = 2 行
        rows, cols = measureElement(comp, terminal_width=6)
        assert rows == 2
        assert cols == 8  # 原始行最大列宽（换行前）

    def test_measure_cjk_multiline(self):
        """多行 CJK 文本各行独立计算。"""

        class CjkMultiLine(TuiComponent):
            def render(self):
                return "你好\n世界"  # 每行 4 列宽

        comp = CjkMultiLine()
        rows, cols = measureElement(comp, terminal_width=3)
        # "你好" = 4 列宽, ceil(4/3) = 2 行
        # "世界" = 4 列宽, ceil(4/3) = 2 行
        assert rows == 4
        assert cols == 4

    def test_measure_mixed_ascii_cjk(self):
        """混合 ASCII 和 CJK 字符正确计算列宽。"""

        class MixedComponent(TuiComponent):
            def render(self):
                return "AB你好"  # A(1)+B(1)+你(2)+好(2)=6

        comp = MixedComponent()
        rows, cols = measureElement(comp)
        assert rows == 1
        assert cols == 6


class TestMeasureTerminalWidth:
    """terminal_width 参数影响行数计算。"""

    def test_measure_terminal_width_80(self):
        """terminal_width=80 时正常测量。"""
        comp = SimpleComponent("a" * 50)
        rows, cols = measureElement(comp, terminal_width=80)
        assert rows == 1  # 50 < 80，不换行
        assert cols == 50

    def test_measure_terminal_width_40(self):
        """terminal_width=40 时多行组件行数增加。"""
        comp = SimpleComponent("a" * 100)
        rows_80, cols_80 = measureElement(comp, terminal_width=80)
        rows_40, cols_40 = measureElement(comp, terminal_width=40)

        # 80 列: ceil(100/80) = 2 行
        assert rows_80 == 2
        # 40 列: ceil(100/40) = 3 行
        assert rows_40 == 3
        # 列宽不受终端宽度影响
        assert cols_40 == 100
        assert cols_80 == 100
        # terminal_width=40 时行数更多
        assert rows_40 > rows_80

    def test_measure_terminal_width_very_narrow(self):
        """极窄终端下大量换行。"""
        comp = SimpleComponent("abcdefghij")  # 10 字符
        rows, cols = measureElement(comp, terminal_width=3)
        # ceil(10/3) = 4 行
        assert rows == 4
        assert cols == 10

    def test_measure_terminal_width_default(self):
        """未指定 terminal_width 时使用默认值 80。"""
        comp = SimpleComponent("a" * 79)
        rows, cols = measureElement(comp)
        assert rows == 1  # 79 < 80 默认
        assert cols == 79


class TestMeasureComponentWithChildren:
    """使用真实 TuiComponent（如 Text）测量。"""

    def test_measure_text_component(self):
        """Text 叶子组件可正常测量。"""
        from src.chat_ui._box import Text

        comp = Text("hello")
        rows, cols = measureElement(comp)
        assert rows == 1
        assert cols == 5

    def test_measure_text_component_with_style(self):
        """带样式的 Text 组件提取纯文本后测量。"""
        from src.chat_ui._box import Text
        from rich.style import Style

        comp = Text("styled text", style=Style(color="red"))
        rows, cols = measureElement(comp)
        # 带 style 时 render() 返回 RichText, .plain → "styled text"
        assert rows == 1
        assert cols == len("styled text")  # 11


# ═══════════════════════════════════════════════════════════
# 运行入口
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
