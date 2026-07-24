"""测试布局控件 — Vertical / Horizontal / Padding / Border / Grid / Center。"""

from __future__ import annotations

from src.tui.render_buffer import RenderBuffer
from src.tui.widget_base import Widget
from src.tui.layout import (
    Vertical,
    Horizontal,
    Padding,
    Border,
    Grid,
    Center,
)


# ═══════════════════════════════════════════════════════════
# 测试辅助：简单文本控件
# ═══════════════════════════════════════════════════════════


class Label(Widget):
    """简单的文本标签控件，用于布局测试。"""
    def __init__(self, text: str, **kwargs):
        super().__init__(**kwargs)
        self._text = text

    def render(self, buffer: RenderBuffer) -> None:
        buffer.write(0, 0, self._text)


class MultiLineLabel(Widget):
    """多行文本标签控件。"""
    def __init__(self, lines: list[str], **kwargs):
        super().__init__(**kwargs)
        self._lines = lines

    def render(self, buffer: RenderBuffer) -> None:
        for i, line in enumerate(self._lines):
            if i < buffer.height:
                buffer.write(0, i, line)


# ═══════════════════════════════════════════════════════════
# Vertical
# ═══════════════════════════════════════════════════════════

class TestVertical:
    """测试 Vertical 垂直布局控件。"""

    def test_basic_vertical_layout(self):
        """垂直排列两个标签。"""
        v = Vertical([Label("A"), Label("B")])
        buf = RenderBuffer(10, 5)
        v.mount()
        v.render(buf)
        result = buf.render()
        lines = result.split("\n")
        assert "A" in lines[0]
        assert "B" in lines[1]

    def test_vertical_with_spacing(self):
        """spacing 参数增加子控件间距。"""
        v = Vertical([Label("A"), Label("B")], spacing=2)
        buf = RenderBuffer(10, 5)
        v.mount()
        v.render(buf)
        result = buf.render()
        lines = result.split("\n")
        assert "A" in lines[0]
        assert "B" in lines[3]  # spacing=2 → A + 2空行 + B

    def test_vertical_align_center(self):
        """align='center' 水平居中。"""
        v = Vertical([Label("Hi")], align="center")
        buf = RenderBuffer(10, 3)
        v.mount()
        v.render(buf)
        result = buf.render()
        lines = result.split("\n")
        # "Hi" 宽度2，10列居中 → 左侧4空格
        assert lines[0].startswith("    Hi")

    def test_vertical_align_right(self):
        """align='right' 右对齐。"""
        v = Vertical([Label("Hi")], align="right")
        buf = RenderBuffer(10, 3)
        v.mount()
        v.render(buf)
        result = buf.render()
        lines = result.split("\n")
        # "Hi" 宽度2，10列右对齐 → 左侧8空格
        assert lines[0].startswith("        Hi")

    def test_vertical_max_height(self):
        """max_height 限制最大高度。"""
        v = Vertical([Label("A"), Label("B"), Label("C")], max_height=2)
        buf = RenderBuffer(10, 5)
        v.mount()
        v.render(buf)
        result = buf.render()
        lines = result.split("\n")
        visible = [l for l in lines if l.strip()]
        # max_height=2 只能显示2个label
        assert len(visible) <= 2

    def test_vertical_empty_children(self):
        """空的子控件列表安全渲染。"""
        v = Vertical([])
        buf = RenderBuffer(10, 3)
        v.mount()
        v.render(buf)  # 不应抛异常

    def test_vertical_repr(self):
        """__repr__ 返回人类可读描述。"""
        v = Vertical([Label("A"), Label("B")])
        assert "Vertical" in repr(v)
        assert "2" in repr(v)


class TestHorizontal:
    """测试 Horizontal 水平布局控件。"""

    def test_basic_horizontal_layout(self):
        """水平排列两个标签。"""
        h = Horizontal([Label("A"), Label("B")], spacing=1)
        buf = RenderBuffer(10, 3)
        h.mount()
        h.render(buf)
        result = buf.render()
        lines = result.split("\n")
        # A + 空格 + B
        assert "A B" in lines[0] or ("A" in lines[0] and "B" in lines[0])

    def test_horizontal_no_spacing(self):
        """spacing=0 时标签紧挨。"""
        h = Horizontal([Label("A"), Label("B")], spacing=0)
        buf = RenderBuffer(10, 3)
        h.mount()
        h.render(buf)
        result = buf.render()
        lines = result.split("\n")
        assert "AB" in lines[0]

    def test_horizontal_align_center(self):
        """align='center' 垂直居中。"""
        h = Horizontal([Label("Hi")], align="center")
        buf = RenderBuffer(10, 5)
        h.mount()
        h.render(buf)
        result = buf.render()
        lines = result.split("\n")
        # 在5行高容器中，第2行（0-based）
        assert "Hi" in lines[2]

    def test_horizontal_align_bottom(self):
        """align='bottom' 底部对齐。"""
        h = Horizontal([Label("Hi")], align="bottom")
        buf = RenderBuffer(10, 5)
        h.mount()
        h.render(buf)
        result = buf.render()
        lines = result.split("\n")
        assert "Hi" in lines[4]

    def test_horizontal_max_width(self):
        """max_width 限制最大宽度。"""
        h = Horizontal([Label("AAAAA"), Label("BBBBB")], spacing=1, max_width=6)
        buf = RenderBuffer(20, 3)
        h.mount()
        h.render(buf)
        # max_width=6 应截断或仅显示第一个标签
        result = buf.render()
        assert "AAAAA" in result

    def test_horizontal_empty_children(self):
        """空子控件列表安全渲染。"""
        h = Horizontal([])
        buf = RenderBuffer(10, 3)
        h.mount()
        h.render(buf)  # 不应抛异常

    def test_horizontal_repr(self):
        """__repr__ 返回人类可读描述。"""
        h = Horizontal([Label("A"), Label("B")])
        assert "Horizontal" in repr(h)
        assert "2" in repr(h)


class TestPadding:
    """测试 Padding 内边距控件。"""

    def test_padding_default(self):
        """默认 padding=1 在左右添加空格。"""
        inner = Label("Hi")
        p = Padding(inner)
        buf = RenderBuffer(10, 3)
        p.mount()
        p.render(buf)
        result = buf.render()
        lines = result.split("\n")
        # padding=1 left → " Hi"
        assert lines[0].startswith(" Hi")

    def test_padding_custom(self):
        """自定义各方向边距。"""
        inner = Label("Hi")
        p = Padding(inner, left=3, right=2, top=1, bottom=1)
        buf = RenderBuffer(10, 5)
        p.mount()
        p.render(buf)
        result = buf.render()
        lines = result.split("\n")
        # top=1 → 第1行 (0-based) 有内容
        assert lines[0].strip() == ""  # top 空行
        assert "Hi" in lines[1]  # 第2行有内容

    def test_padding_no_space(self):
        """容器太小时候安全处理。"""
        inner = Label("Hi")
        p = Padding(inner, left=10, right=10)
        buf = RenderBuffer(5, 3)
        p.mount()
        p.render(buf)  # 不应抛异常（inner_w <= 0）

    def test_padding_repr(self):
        """__repr__ 返回人类可读描述。"""
        inner = Label("Hi")
        p = Padding(inner, left=2, right=2)
        assert "Padding" in repr(p)
        assert "l=2" in repr(p)


class TestBorder:
    """测试 Border 边框控件。"""

    def test_border_rounded(self):
        """rounded 样式边框包含圆角字符。"""
        inner = Label("Hi")
        b = Border(inner, style="rounded")
        buf = RenderBuffer(8, 4)
        b.mount()
        b.render(buf)
        result = buf.render()
        lines = result.split("\n")
        # 顶部边框含 ╭ 字符
        assert "╭" in lines[0] or "─" in lines[0]
        # 底部边框含 ╰ 字符
        assert "╰" in lines[-1] or "─" in lines[-1]

    def test_border_with_title(self):
        """标题显示在顶部边框。"""
        inner = Label("Hi")
        b = Border(inner, style="rounded", title="Test")
        buf = RenderBuffer(12, 4)
        b.mount()
        b.render(buf)
        result = buf.render()
        lines = result.split("\n")
        assert "[ Test ]" in lines[0]

    def test_border_with_title_center(self):
        """标题居中。"""
        inner = Label("Hi")
        b = Border(inner, style="rounded", title="X", title_align="center")
        buf = RenderBuffer(12, 4)
        b.mount()
        b.render(buf)
        result = buf.render()
        lines = result.split("\n")
        assert "[ X ]" in lines[0]

    def test_border_with_title_right(self):
        """标题右对齐。"""
        inner = Label("Hi")
        b = Border(inner, style="rounded", title="X", title_align="right")
        buf = RenderBuffer(12, 4)
        b.mount()
        b.render(buf)
        result = buf.render()
        lines = result.split("\n")
        assert "[ X ]" in lines[0]

    def test_border_with_title_too_long(self):
        """标题过长时截断。"""
        inner = Label("Hi")
        b = Border(inner, style="rounded", title="A" * 20)
        buf = RenderBuffer(8, 4)
        b.mount()
        b.render(buf)  # 不应抛异常

    def test_border_with_color(self):
        """border_color 传递到边框渲染（不验证 ANSI 输出精确性）。"""
        inner = Label("Hi")
        b = Border(inner, style="rounded", border_color=45)
        buf = RenderBuffer(8, 4)
        b.mount()
        b.render(buf)  # 不应抛异常
        assert b._props.get("border_color") == 45

    def test_border_narrow_buffer(self):
        """窄缓冲区安全处理。"""
        inner = Label("Hi")
        b = Border(inner, style="rounded")
        buf = RenderBuffer(2, 2)
        b.mount()
        b.render(buf)  # 不应抛异常（inner_w <= 0）

    def test_border_empty_buffer(self):
        """空缓冲区安全处理。"""
        inner = Label("Hi")
        b = Border(inner, style="rounded")
        buf = RenderBuffer(0, 0)
        b.mount()
        b.render(buf)  # 不应抛异常

    def test_border_invalid_style(self):
        """无效边框样式降级处理。"""
        inner = Label("Hi")
        b = Border(inner, style="nonexistent")
        buf = RenderBuffer(8, 4)
        b.mount()
        b.render(buf)  # 不应抛异常

    def test_border_repr(self):
        """__repr__ 返回人类可读描述。"""
        inner = Label("Hi")
        b = Border(inner, style="rounded")
        assert "Border" in repr(b)
        assert "rounded" in repr(b)


class TestGrid:
    """测试 Grid 网格布局控件。"""

    def test_basic_grid(self):
        """简单网格布局。"""
        g = Grid([
            [Label("A"), Label("B")],
            [Label("C"), Label("D")],
        ], spacing=1)
        buf = RenderBuffer(10, 5)
        g.mount()
        g.render(buf)
        result = buf.render()
        lines = result.split("\n")
        assert "A" in lines[0]
        assert "B" in lines[0]
        assert "C" in lines[1]
        assert "D" in lines[1]

    def test_grid_empty(self):
        """空网格安全。"""
        g = Grid([])
        buf = RenderBuffer(10, 3)
        g.mount()
        g.render(buf)  # 不应抛异常

    def test_grid_repr(self):
        """__repr__ 返回人类可读描述。"""
        g = Grid([[Label("A")]])
        assert "Grid" in repr(g)

    def test_grid_cols_param(self):
        """cols 参数指定列数。"""
        g = Grid([
            [Label("A"), Label("B"), Label("C")],
        ], cols=3, spacing=1)
        buf = RenderBuffer(15, 3)
        g.mount()
        g.render(buf)
        result = buf.render()
        assert "A" in result
        assert "B" in result
        assert "C" in result

    def test_grid_valign_center(self):
        """valign='center' 垂直居中。"""
        g = Grid([
            [Label("Hi")],
            [MultiLineLabel(["A", "B"])],
        ], spacing=1, valign="center")
        buf = RenderBuffer(10, 5)
        g.mount()
        g.render(buf)  # 不应抛异常


class TestCenter:
    """测试 Center 居中对齐容器。"""

    def test_center_both(self):
        """水平和垂直居中。"""
        inner = Label("Hi")
        c = Center(inner, axis="both")
        buf = RenderBuffer(10, 5)
        c.mount()
        c.render(buf)
        result = buf.render()
        lines = result.split("\n")
        # "Hi" 宽度2，10列 → x偏移4；5行 → y偏移2
        assert "Hi" in lines[2]
        assert lines[2].startswith("    Hi")

    def test_center_horizontal_only(self):
        """仅水平居中。"""
        inner = Label("Hi")
        c = Center(inner, axis="horizontal")
        buf = RenderBuffer(10, 5)
        c.mount()
        c.render(buf)
        result = buf.render()
        lines = result.split("\n")
        # 水平居中但垂直不居中（top对齐）
        assert "Hi" in lines[0]
        assert lines[0].startswith("    Hi")

    def test_center_vertical_only(self):
        """仅垂直居中。"""
        inner = Label("Hi")
        c = Center(inner, axis="vertical")
        buf = RenderBuffer(10, 5)
        c.mount()
        c.render(buf)
        result = buf.render()
        lines = result.split("\n")
        # 垂直居中 → 第2行，水平不居中 → left对齐
        assert "Hi" in lines[2]

    def test_center_empty_buffer(self):
        """空缓冲区安全处理。"""
        inner = Label("Hi")
        c = Center(inner)
        buf = RenderBuffer(0, 0)
        c.mount()
        c.render(buf)  # 不应抛异常

    def test_center_repr(self):
        """__repr__ 返回人类可读描述。"""
        inner = Label("Hi")
        c = Center(inner)
        assert "Center" in repr(c)


class TestLayoutComposition:
    """布局控件嵌套组合测试。"""

    def test_vertical_in_horizontal(self):
        """Vertical 嵌套在 Horizontal 中。"""
        v = Vertical([Label("A"), Label("B")], spacing=0)
        h = Horizontal([Label("X"), v], spacing=1)
        buf = RenderBuffer(10, 5)
        h.mount()
        h.render(buf)
        result = buf.render()
        lines = result.split("\n")
        # X 与 A 在同一行
        assert "X" in lines[0]
        assert "A" in lines[0]
        # B 在下一行
        assert "B" in lines[1]

    def test_padding_inside_border(self):
        """Padding 嵌套在 Border 中。"""
        inner = Label("Hi")
        p = Padding(inner, left=2, right=2, top=1, bottom=1)
        b = Border(p, style="rounded")
        buf = RenderBuffer(12, 6)
        b.mount()
        b.render(buf)
        result = buf.render()
        lines = result.split("\n")
        # 顶部边框
        assert "╭" in lines[0] or "─" in lines[0]
        # 底部边框
        assert "╰" in lines[-1] or "─" in lines[-1]
        # "Hi" 应该出现在边框内部（左右各有 padding 2 + 边框1）
        inner_found = any("Hi" in l and "╭" not in l and "╰" not in l for l in lines)
        assert inner_found

    def test_center_inside_vertical(self):
        """Center 嵌套在 Vertical 中。"""
        c = Center(Label("Hi"), axis="horizontal")
        v = Vertical([Label("A"), c], spacing=1)
        buf = RenderBuffer(10, 5)
        v.mount()
        v.render(buf)
        result = buf.render()
        lines = result.split("\n")
        assert "A" in lines[0]
        # Center 水平居中 "Hi"（2字符宽，10列 → x=4），Vertical 中 spacing=1 后在第2行
        assert "Hi" in lines[2]
