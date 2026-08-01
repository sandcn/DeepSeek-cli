"""测试 ink/layout.py — flexbox 子集 + 文本换行。

纯函数断言（无终端依赖）：直接构造 host fiber 树并测量。
"""

from __future__ import annotations

from src.tui.ink.element import h, BOX, TEXT, SPACER
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.layout import layout_tree, wrap_text_lines
from src.tui.ink.components import render_frame


def _render_and_layout(root_element, width):
    """渲染元素树并返回 (root_fiber, doc_height)。"""
    r = Reconciler()
    root = r.create_root()
    r.render(root, root_element, width, 24)
    return root, root.child.layout_box


class TestLayoutBoxes:
    """布局盒分配。"""

    def test_single_text(self):
        root, box = _render_and_layout(h(TEXT, {"children": "abc"}), 80)
        assert box.w == 80
        assert box.h == 1

    def test_text_wraps_to_multiple_lines(self):
        root, box = _render_and_layout(h(TEXT, {"children": "a" * 30}), 10)
        assert box.w == 10
        assert box.h == 3

    def test_explicit_width(self):
        root, box = _render_and_layout(h(TEXT, {"children": "abc", "width": 5}), 80)
        assert box.w == 5

    def test_spacer_height(self):
        root, box = _render_and_layout(h(SPACER, {"height": 3}), 80)
        assert box.h == 3

    def test_column_stack(self):
        root, box = _render_and_layout(
            h(BOX, None,
              h(TEXT, {"children": "a"}),
              h(TEXT, {"children": "b"})),
            80,
        )
        assert box.h == 2
        texts = []
        child = root.child.child
        while child:
            texts.append(child)
            child = child.sibling
        # 纵向堆叠：第二个 text y = 第一个 y + 1
        assert texts[0].layout_box.y == 0
        assert texts[1].layout_box.y == 1

    def test_padding_increases_height(self):
        root, box = _render_and_layout(
            h(BOX, {"padding": 1}, h(TEXT, {"children": "x"})),
            80,
        )
        # 内容 1 行 + 上下各 1 padding = 3
        assert box.h == 3

    def test_explicit_height_overrides(self):
        root, box = _render_and_layout(
            h(BOX, {"height": 5}, h(TEXT, {"children": "x"})),
            80,
        )
        assert box.h == 5

    def test_row_direction(self):
        """row 布局：子节点横向排列，高度为最大子高。"""
        root, box = _render_and_layout(
            h(BOX, {"flexDirection": "row"},
              h(TEXT, {"children": "ab"}),
              h(TEXT, {"children": "c"})),
            80,
        )
        assert box.h == 1
        texts = []
        child = root.child.child
        while child:
            texts.append(child)
            child = child.sibling
        assert texts[0].layout_box.x == 0
        assert texts[1].layout_box.x == 2  # 第一个宽 2


class TestWrapTextLines:
    """文本换行。"""

    def test_no_wrap_when_fits(self):
        lines = wrap_text_lines("abc", 10)
        assert len(lines) == 1
        assert lines[0].plain == "abc"

    def test_wrap_wide(self):
        lines = wrap_text_lines("abcdefgh", 3)
        assert [l.plain for l in lines] == ["abc", "def", "gh"]

    def test_wrap_cjk(self):
        lines = wrap_text_lines("中文字测试", 4)
        joined = "".join(l.plain for l in lines)
        assert joined == "中文字测试"
        # 每行宽度 <= 4
        assert all(len(l.plain) * 2 <= 4 for l in lines)

    def test_empty(self):
        lines = wrap_text_lines("", 10)
        assert lines == []


class TestRenderFrame:
    """render_frame 整帧输出。"""

    def test_frame_from_tree(self):
        root = Reconciler().create_root()
        el = h(BOX, None, h(TEXT, {"children": "hello"}))
        r = Reconciler()
        r.render(root, el, 80, 24)
        frame = render_frame(root, 80)
        assert frame.height >= 1
        assert frame.lines[0].plain == "hello"

    def test_border_box(self):
        root = Reconciler().create_root()
        el = h(BOX, {"border": 1, "width": 5}, h(TEXT, {"children": "x"}))
        r = Reconciler()
        r.render(root, el, 80, 24)
        frame = render_frame(root, 80)
        # 边框盒高 = 2 border + 1 内容 = 3
        assert frame.height == 3
        top = frame.lines[0].plain
        assert top.startswith("┌")
        assert top.endswith("┐")
        mid = frame.lines[1].plain
        assert mid.startswith("│")
        bot = frame.lines[2].plain
        assert bot.startswith("└")

    def test_static_wraps_children(self):
        root = Reconciler().create_root()
        el = h("static", None, h(TEXT, {"children": "committed"}))
        r = Reconciler()
        r.render(root, el, 80, 24)
        frame = render_frame(root, 80)
        assert frame.lines[0].plain == "committed"
