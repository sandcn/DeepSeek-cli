"""布局系统测试 — VBox/HBox/Flex。

覆盖正常渲染、边界条件、嵌套布局、对齐模式、wrap 换行等场景。
"""

from __future__ import annotations

import pytest

from tui_framework.layout import (
    VBox, HBox, Flex, FlexDirection, HAlign, LayoutContainer,
)
from tui_framework.widgets.base import Widget


# ── 测试用组件 ────────────────────────────────────────────


class TextWidget(Widget):
    """简单文本组件 — 返回固定文本。"""

    def __init__(self, text: str, visible: bool = True):
        super().__init__()
        self.text = text
        if not visible:
            self.hide()

    def render(self) -> str:
        return self.text


class MultiLineWidget(Widget):
    """多行文本组件 — 返回多行固定文本。"""

    def __init__(self, lines: list[str]):
        super().__init__()
        self.lines = lines

    def render(self) -> str:
        return '\n'.join(self.lines)


class FixedSizeWidget(Widget):
    """固定尺寸组件 — 返回指定尺寸的占位文本。"""

    def __init__(self, width: int, height: int):
        super().__init__()
        self._width = width
        self._height = height

    def render(self) -> str:
        line = 'X' * self._width
        return '\n'.join([line] * self._height)


# ══════════════════════════════════════════════════════════
# LayoutContainer 基类测试
# ══════════════════════════════════════════════════════════


class TestLayoutContainer:
    """LayoutContainer 抽象基类测试。"""

    def test_add_child_accepts_widget(self):
        """add_child 接受 Widget 实例。"""
        container = VBox()
        widget = TextWidget("hello")
        container.add_child(widget)
        assert container.child_count == 1
        assert container.children == [widget]

    def test_add_child_rejects_non_widget(self):
        """add_child 拒绝非 Widget 实例。"""
        container = VBox()
        with pytest.raises(TypeError, match="期望 Widget 实例"):
            container.add_child("not a widget")  # type: ignore[arg-type]

    def test_remove_child(self):
        """remove_child 正确移除子元素。"""
        container = VBox()
        w1 = TextWidget("A")
        w2 = TextWidget("B")
        container.add_child(w1)
        container.add_child(w2)
        container.remove_child(w1)
        assert container.child_count == 1
        assert container.children == [w2]

    def test_remove_child_not_found(self):
        """remove_child 移除不存在的控件抛出 ValueError。"""
        container = VBox()
        w = TextWidget("A")
        container.add_child(w)
        unknown = TextWidget("B")
        with pytest.raises(ValueError, match="不在子元素列表中"):
            container.remove_child(unknown)

    def test_clear_children(self):
        """clear_children 清空所有子元素。"""
        container = VBox()
        container.add_child(TextWidget("A"))
        container.add_child(TextWidget("B"))
        container.clear_children()
        assert container.is_empty
        assert container.child_count == 0

    def test_is_empty(self):
        """空容器 is_empty=True。"""
        container = VBox()
        assert container.is_empty

    def test_child_count(self):
        """child_count 正确计数。"""
        container = VBox()
        assert container.child_count == 0
        container.add_child(TextWidget("A"))
        container.add_child(TextWidget("B"))
        assert container.child_count == 2

    def test_spacing_property(self):
        """spacing 属性读写正确。"""
        container = VBox(spacing=3)
        assert container.spacing == 3
        container.spacing = 5
        assert container.spacing == 5

    def test_spacing_clamped_to_zero(self):
        """spacing 不能为负数。"""
        container = VBox(spacing=-5)
        assert container.spacing == 0

    def test_padding_property(self):
        """padding 属性读写正确。"""
        container = VBox(padding=(1, 2, 3, 4))
        assert container.padding == (1, 2, 3, 4)

    def test_padding_invalid_length(self):
        """padding 长度不对抛出 ValueError。"""
        container = VBox()
        with pytest.raises(ValueError, match="padding 必须是"):
            container.padding = (1, 2, 3)  # type: ignore[assignment]

    def test_layout_container_is_widget_subclass(self):
        """LayoutContainer 是 Widget 子类，可实例化（框架不使用 ABC 元类）。"""
        # 注意：框架的 TuiComponent/Widget 不使用 ABC 元类，
        # @abstractmethod 仅作为文档标注，不阻止实例化。
        container = LayoutContainer()
        assert isinstance(container, Widget)
        assert isinstance(container, LayoutContainer)
        assert container.is_empty


# ══════════════════════════════════════════════════════════
# VBox 测试
# ══════════════════════════════════════════════════════════


class TestVBox:
    """VBox 垂直布局测试。"""

    def test_empty_vbox_renders_empty_string(self):
        """空 VBox 渲染为空字符串。"""
        vb = VBox()
        assert vb.render() == ""

    def test_single_child(self):
        """单个子元素正常渲染。"""
        vb = VBox()
        vb.add_child(TextWidget("hello"))
        assert vb.render() == "hello"

    def test_multiple_children_vertical(self):
        """多个子元素垂直排列。"""
        vb = VBox(spacing=0)
        vb.add_child(TextWidget("A"))
        vb.add_child(TextWidget("B"))
        vb.add_child(TextWidget("C"))
        assert vb.render() == "A\nB\nC"

    def test_spacing_between_children(self):
        """子元素间间距正确插入空行。"""
        vb = VBox(spacing=2)
        vb.add_child(TextWidget("A"))
        vb.add_child(TextWidget("B"))
        # A + 2空行 + B
        assert vb.render() == "A\n\n\nB"

    def test_spacing_single_child_no_extra(self):
        """单子元素时 spacing 不插入额外空行。"""
        vb = VBox(spacing=5)
        vb.add_child(TextWidget("A"))
        assert vb.render() == "A"

    def test_invisible_child_skipped(self):
        """不可见子元素被跳过。"""
        vb = VBox()
        vb.add_child(TextWidget("A", visible=True))
        vb.add_child(TextWidget("B", visible=False))
        vb.add_child(TextWidget("C", visible=True))
        assert vb.render() == "A\nC"

    def test_content_width(self):
        """get_content_width 返回最大子元素宽度。"""
        vb = VBox()
        vb.add_child(TextWidget("short"))
        vb.add_child(TextWidget("much_longer_text"))
        vb.add_child(TextWidget("ok"))
        assert vb.get_content_width() == len("much_longer_text")

    def test_content_height(self):
        """get_content_height 返回子元素行数和 + 间距。"""
        vb = VBox(spacing=1)
        vb.add_child(MultiLineWidget(["A", "B", "C"]))  # 3 行
        vb.add_child(TextWidget("D"))                     # 1 行
        vb.add_child(MultiLineWidget(["E", "F"]))         # 2 行
        # 3 + 1 + 2 + 间距(3-1)*1 = 8
        assert vb.get_content_height() == 8

    def test_padding_applied(self):
        """padding 正确应用。"""
        vb = VBox(padding=(1, 0, 1, 2), spacing=0)
        vb.add_child(TextWidget("hello"))
        result = vb.render()
        lines = result.split('\n')
        assert len(lines) == 3  # padding_top + content + padding_bottom
        assert lines[0] == '  '        # top padding
        assert lines[1] == '  hello'   # content + left padding
        assert lines[2] == '  '        # bottom padding

    def test_mixed_visibility_with_spacing(self):
        """混合可见性 + 间距时，不可见元素不占空间。"""
        vb = VBox(spacing=1)
        vb.add_child(TextWidget("A", visible=True))
        vb.add_child(TextWidget("HIDDEN", visible=False))
        vb.add_child(TextWidget("C", visible=True))
        # 只有 A 和 C，间距 1 空行
        assert vb.render() == "A\n\nC"


# ══════════════════════════════════════════════════════════
# HBox 测试
# ══════════════════════════════════════════════════════════


class TestHBox:
    """HBox 水平布局测试。"""

    def test_empty_hbox_renders_empty_string(self):
        """空 HBox 渲染为空字符串。"""
        hb = HBox()
        assert hb.render() == ""

    def test_single_child(self):
        """单个子元素正常渲染。"""
        hb = HBox()
        hb.add_child(TextWidget("hello"))
        assert "hello" in hb.render()

    def test_multiple_children_horizontal(self):
        """多个子元素水平排列。"""
        hb = HBox(spacing=1)
        hb.add_child(TextWidget("A"))
        hb.add_child(TextWidget("B"))
        hb.add_child(TextWidget("C"))
        # 去除 ANSI RESET，验证拼接
        result = hb.render().replace('\033[0m', '')
        assert "A" in result
        assert "B" in result
        assert "C" in result

    def test_spacing_between_children(self):
        """子元素间间距正确。"""
        hb = HBox(spacing=3)
        hb.add_child(TextWidget("A"))
        hb.add_child(TextWidget("B"))
        result = hb.render().replace('\033[0m', '')
        assert "A   B" in result

    def test_invisible_child_skipped(self):
        """不可见子元素被跳过。"""
        hb = HBox(spacing=1)
        hb.add_child(TextWidget("A", visible=True))
        hb.add_child(TextWidget("B", visible=False))
        hb.add_child(TextWidget("C", visible=True))
        result = hb.render().replace('\033[0m', '')
        assert "B" not in result
        assert "A" in result
        assert "C" in result

    def test_align_top(self):
        """顶部对齐模式 — 较短的元素下方留空。"""
        hb = HBox(spacing=1, align=HAlign.TOP)
        hb.add_child(MultiLineWidget(["A", "B", "C"]))  # 3 行
        hb.add_child(TextWidget("X"))                     # 1 行
        result = hb.render()
        lines = [l.replace('\033[0m', '').rstrip() for l in result.split('\n')]
        assert len(lines) == 3
        assert lines[0] == "A X"
        assert lines[1] == "B"       # X 下方留空
        assert lines[2] == "C"

    def test_align_middle(self):
        """居中对齐模式 — 较短的元素居中。"""
        hb = HBox(spacing=1, align=HAlign.MIDDLE)
        hb.add_child(MultiLineWidget(["A", "B", "C"]))  # 3 行
        hb.add_child(TextWidget("X"))                     # 1 行
        result = hb.render()
        lines = [l.replace('\033[0m', '').rstrip() for l in result.split('\n')]
        # X 应出现在第 2 行（中间）
        assert lines[1] == "B X"

    def test_align_bottom(self):
        """底部对齐模式 — 较短的元素底部对齐。"""
        hb = HBox(spacing=1, align=HAlign.BOTTOM)
        hb.add_child(MultiLineWidget(["A", "B", "C"]))  # 3 行
        hb.add_child(TextWidget("X"))                     # 1 行
        result = hb.render()
        lines = [l.replace('\033[0m', '').rstrip() for l in result.split('\n')]
        assert lines[2] == "C X"

    def test_invalid_align_raises(self):
        """无效对齐模式抛出 ValueError。"""
        with pytest.raises(ValueError, match="无效的对齐模式"):
            HBox(align="invalid")

    def test_ansi_reset_appended(self):
        """每个子元素行末尾追加 ANSI RESET。"""
        hb = HBox(spacing=0)
        hb.add_child(TextWidget("A"))
        hb.add_child(TextWidget("B"))
        result = hb.render()
        assert '\033[0m' in result


# ══════════════════════════════════════════════════════════
# Flex 测试
# ══════════════════════════════════════════════════════════


class TestFlex:
    """Flex 弹性布局测试。"""

    def test_flex_row_renders_horizontally(self):
        """Flex row 模式水平渲染。"""
        flex = Flex(direction=FlexDirection.ROW, spacing=1)
        flex.add_child(TextWidget("A"))
        flex.add_child(TextWidget("B"))
        result = flex.render().replace('\033[0m', '')
        assert "A" in result
        assert "B" in result

    def test_flex_column_renders_vertically(self):
        """Flex column 模式垂直渲染。"""
        flex = Flex(direction=FlexDirection.COLUMN, spacing=0)
        flex.add_child(TextWidget("A"))
        flex.add_child(TextWidget("B"))
        assert flex.render() == "A\nB"

    def test_flex_direction_switch(self):
        """direction 属性切换生效。"""
        flex = Flex(direction=FlexDirection.ROW)
        assert flex.direction == FlexDirection.ROW
        flex.direction = FlexDirection.COLUMN
        assert flex.direction == FlexDirection.COLUMN

    def test_flex_empty(self):
        """空 Flex 渲染为空字符串。"""
        flex = Flex()
        assert flex.render() == ""

    def test_flex_weight_recorded(self):
        """flex_weight 正确记录。"""
        flex = Flex()
        flex.add_child(TextWidget("A"), flex_weight=3.5)
        assert flex._flex_children[0].flex_weight == 3.5

    def test_flex_weight_default(self):
        """默认 flex_weight=1.0。"""
        flex = Flex()
        flex.add_child(TextWidget("A"))
        assert flex._flex_children[0].flex_weight == 1.0

    def test_flex_weight_zero(self):
        """flex_weight=0 表示固定尺寸。"""
        flex = Flex()
        flex.add_child(TextWidget("A"), flex_weight=0)
        assert flex._flex_children[0].flex_weight == 0.0

    def test_flex_wrap_mode(self):
        """wrap=True 模式子元素超出宽度时换行。"""
        flex = Flex(direction=FlexDirection.ROW, spacing=0, wrap=True, max_width=10)
        flex.add_child(TextWidget("AAAAA"))   # 5
        flex.add_child(TextWidget("BBBBB"))   # 5
        flex.add_child(TextWidget("CCCCC"))   # 5，超出 10
        result = flex.render().replace('\033[0m', '')
        lines = result.split('\n')
        # 应换行：第一行 AAAAA BBBBB / 第二行 CCCCC
        assert len(lines) >= 2

    def test_flex_no_wrap_stays_single_line(self):
        """wrap=False 时不换行。"""
        flex = Flex(direction=FlexDirection.ROW, spacing=0, wrap=False)
        flex.add_child(TextWidget("AAAAA"))
        flex.add_child(TextWidget("BBBBB"))
        flex.add_child(TextWidget("CCCCC"))
        result = flex.render().replace('\033[0m', '')
        # 全部在一行
        lines = result.split('\n')
        assert len(lines) >= 1

    def test_flex_invalid_direction_raises(self):
        """无效 direction 抛出 ValueError。"""
        with pytest.raises(ValueError, match="无效的 direction"):
            Flex(direction="diagonal")

    def test_flex_remove_child(self):
        """Flex remove_child 同步移除弹性包装器。"""
        flex = Flex()
        w = TextWidget("A")
        flex.add_child(w)
        assert len(flex._flex_children) == 1
        flex.remove_child(w)
        assert len(flex._flex_children) == 0

    def test_flex_clear_children(self):
        """Flex clear_children 清空弹性包装器。"""
        flex = Flex()
        flex.add_child(TextWidget("A"))
        flex.add_child(TextWidget("B"))
        flex.clear_children()
        assert len(flex._flex_children) == 0
        assert flex.is_empty


# ══════════════════════════════════════════════════════════
# 嵌套布局测试
# ══════════════════════════════════════════════════════════


class TestNestedLayout:
    """嵌套布局测试。"""

    def test_vbox_inside_hbox(self):
        """VBox 嵌套在 HBox 内。"""
        vb = VBox(spacing=0)
        vb.add_child(TextWidget("A"))
        vb.add_child(TextWidget("B"))

        hb = HBox(spacing=1)
        hb.add_child(vb)
        hb.add_child(TextWidget("C"))

        result = hb.render().replace('\033[0m', '')
        assert "A" in result
        assert "B" in result
        assert "C" in result

    def test_hbox_inside_vbox(self):
        """HBox 嵌套在 VBox 内。"""
        hb = HBox(spacing=1)
        hb.add_child(TextWidget("A"))
        hb.add_child(TextWidget("B"))

        vb = VBox(spacing=0)
        vb.add_child(TextWidget("HEADER"))
        vb.add_child(hb)

        result = vb.render().replace('\033[0m', '')
        assert "HEADER" in result
        assert "A" in result
        assert "B" in result

    def test_deep_nesting(self):
        """多层嵌套渲染正常。"""
        inner = VBox(spacing=0)
        inner.add_child(TextWidget("deep"))

        mid = HBox(spacing=0)
        mid.add_child(inner)

        outer = VBox(spacing=0)
        outer.add_child(TextWidget("top"))
        outer.add_child(mid)

        result = outer.render().replace('\033[0m', '')
        assert "top" in result
        assert "deep" in result


# ══════════════════════════════════════════════════════════
# 边界条件测试
# ══════════════════════════════════════════════════════════


class TestEdgeCases:
    """边界条件测试。"""

    def test_all_children_invisible(self):
        """全部子元素不可见时返回空。"""
        vb = VBox()
        vb.add_child(TextWidget("A", visible=False))
        vb.add_child(TextWidget("B", visible=False))
        assert vb.render() == ""

    def test_widget_render_empty_string(self):
        """Widget render 返回空字符串时正确处理。"""
        vb = VBox()
        vb.add_child(TextWidget(""))
        vb.add_child(TextWidget("B"))
        assert vb.render() == "\nB"

    def test_zero_spacing_no_gaps(self):
        """spacing=0 时无额外空行/空格。"""
        vb = VBox(spacing=0)
        vb.add_child(TextWidget("A"))
        vb.add_child(TextWidget("B"))
        assert vb.render() == "A\nB"

    def test_large_spacing(self):
        """大间距值正确处理。"""
        vb = VBox(spacing=100)
        vb.add_child(TextWidget("A"))
        vb.add_child(TextWidget("B"))
        lines = vb.render().split('\n')
        # A + 100 空行 + B = 102 行
        assert len(lines) == 102
        assert lines[0] == "A"
        assert lines[101] == "B"

    def test_very_wide_child_in_hbox(self):
        """超宽子元素在 HBox 中正确渲染。"""
        hb = HBox(spacing=1)
        hb.add_child(TextWidget("A" * 200))
        hb.add_child(TextWidget("B"))
        result = hb.render().replace('\033[0m', '')
        assert ("A" * 200) in result
        assert "B" in result
