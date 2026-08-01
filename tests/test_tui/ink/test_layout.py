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


class TestFlexGrowRemainder:
    """方向C 步骤6 — flexGrow 余数分配（总高度不变，余数到前 n 个子节点）。"""

    def _collect_texts(self, root):
        texts = []
        child = root.child.child
        while child:
            texts.append(child)
            child = child.sibling
        return texts

    def test_flexgrow_remainder_distribution(self):
        """remaining=8, grow=[2,1] → per=2, remainder=2 → extra=[5,3]。"""
        root, box = _render_and_layout(
            h(BOX, {"height": 10},
              h(TEXT, {"children": "a", "flexGrow": 2}),
              h(TEXT, {"children": "b", "flexGrow": 1})),
            80,
        )
        texts = self._collect_texts(root)
        assert texts[0].layout_box.h == 6  # 1 内容 + 5 富余
        assert texts[1].layout_box.h == 4  # 1 内容 + 3 富余
        assert box.h == 10  # 总高度不变

    def test_flexgrow_remainder_goes_to_first_n(self):
        """remaining=5, grow=[1,1,1] → per=1, remainder=2 → extra=[2,2,1]。"""
        root, box = _render_and_layout(
            h(BOX, {"height": 8},
              h(TEXT, {"children": "a", "flexGrow": 1}),
              h(TEXT, {"children": "b", "flexGrow": 1}),
              h(TEXT, {"children": "c", "flexGrow": 1})),
            80,
        )
        texts = self._collect_texts(root)
        assert [t.layout_box.h for t in texts] == [3, 3, 2]
        assert box.h == 8

    def test_flexgrow_equal_grow_balanced(self):
        """remaining=6, grow=[1,1,1] → per=2, remainder=0 → extra=[2,2,2]。"""
        root, box = _render_and_layout(
            h(BOX, {"height": 9},
              h(TEXT, {"children": "a", "flexGrow": 1}),
              h(TEXT, {"children": "b", "flexGrow": 1}),
              h(TEXT, {"children": "c", "flexGrow": 1})),
            80,
        )
        texts = self._collect_texts(root)
        assert [t.layout_box.h for t in texts] == [3, 3, 3]
        assert box.h == 9


class TestFlexShrink:
    """方向2 U4 — flexShrink 收缩（与 flexGrow 余数分配对称）。"""

    def _collect_texts(self, root):
        texts = []
        child = root.child.child
        while child:
            texts.append(child)
            child = child.sibling
        return texts

    def test_flexshrink_reduces_children_to_fit(self):
        """BOX(height=2) + 两个 flexShrink=1 的 3 行 TEXT → 总高 2、子节点高度收缩。"""
        root, box = _render_and_layout(
            h(BOX, {"height": 2},
              h(TEXT, {"children": "a" * 30, "flexShrink": 1}),
              h(TEXT, {"children": "b" * 30, "flexShrink": 1})),
            10,
        )
        texts = self._collect_texts(root)
        assert box.h == 2
        # deficit=4, shrink=[1,1] → per=2 → 每子缩减 2 行 → [1,1]
        assert [t.layout_box.h for t in texts] == [1, 1]
        # y 重新堆叠（无重叠）
        assert texts[0].layout_box.y == 0
        assert texts[1].layout_box.y == 1

    def test_flexshrink_no_shrink_unchanged(self):
        """无 flexShrink 时子节点高度/位置不变（行为零变化）。"""
        root, box = _render_and_layout(
            h(BOX, {"height": 2},
              h(TEXT, {"children": "a" * 30}),
              h(TEXT, {"children": "b" * 30})),
            10,
        )
        texts = self._collect_texts(root)
        assert box.h == 2
        # 子节点保持自然高度（3 行）与自然 y（内容溢出容器底部，不改动）
        assert [t.layout_box.h for t in texts] == [3, 3]
        assert texts[0].layout_box.y == 0
        assert texts[1].layout_box.y == 3

    def test_flexshrink_weight_distribution(self):
        """shrink 权重分配：shrink=[2,1]、deficit=3 → 缩减 [2,1] 行。"""
        root, box = _render_and_layout(
            h(BOX, {"height": 3},
              h(TEXT, {"children": "a" * 30, "flexShrink": 2}),
              h(TEXT, {"children": "b" * 30, "flexShrink": 1})),
            10,
        )
        texts = self._collect_texts(root)
        assert box.h == 3
        # child0: 3-2=1；child1: 3-1=2（总 3 = 容器高）
        assert [t.layout_box.h for t in texts] == [1, 2]

    def test_flexshrink_single_child_min_clamp(self):
        """单子钳制 ≥1：BOX(height=1) + 5 行 TEXT shrink=1 → 子高 1。"""
        root, box = _render_and_layout(
            h(BOX, {"height": 1},
              h(TEXT, {"children": "a" * 50, "flexShrink": 1})),
            10,
        )
        texts = self._collect_texts(root)
        assert box.h == 1
        assert texts[0].layout_box.h == 1


class TestTextWrapTruncate:
    """方向B 步骤12 — textWrap 模式（truncate 省略号）。"""

    def _frame_plain(self, element, width):
        """渲染元素并返回整帧纯文本行列表。"""
        r = Reconciler()
        root = r.create_root()
        r.render(root, element, width, 24)
        frame = render_frame(root, width)
        return [line.plain for line in frame.lines]

    def test_textwrap_truncate_single_line_with_ellipsis(self):
        """textWrap='truncate'：高度为 1 行且内容截断+省略号。"""
        root, box = _render_and_layout(
            h(TEXT, {"children": "a" * 30, "textWrap": "truncate"}), 10
        )
        assert box.h == 1  # 单行
        lines = self._frame_plain(h(TEXT, {"children": "a" * 30, "textWrap": "truncate"}), 10)
        assert lines == ["a" * 9 + "…"]  # 9 个 a + 省略号（宽度 10）

    def test_textwrap_truncate_fits_no_ellipsis(self):
        """textWrap='truncate'：内容未超宽 → 原样单行（无省略号）。"""
        root, box = _render_and_layout(
            h(TEXT, {"children": "abc", "textWrap": "truncate"}), 10
        )
        assert box.h == 1
        lines = self._frame_plain(h(TEXT, {"children": "abc", "textWrap": "truncate"}), 10)
        assert lines == ["abc"]

    def test_textwrap_truncate_end_same_as_truncate(self):
        """textWrap='truncate-end' 与 'truncate' 同语义（末尾省略号）。"""
        root, box = _render_and_layout(
            h(TEXT, {"children": "b" * 20, "textWrap": "truncate-end"}), 6
        )
        assert box.h == 1
        lines = self._frame_plain(h(TEXT, {"children": "b" * 20, "textWrap": "truncate-end"}), 6)
        assert lines == ["b" * 5 + "…"]

    def test_textwrap_truncate_start_keeps_tail(self):
        """textWrap='truncate-start'：省略号在开头，保留尾部（react-ink 语义）。"""
        root, box = _render_and_layout(
            h(TEXT, {"children": "a" * 20, "textWrap": "truncate-start"}), 6
        )
        assert box.h == 1
        lines = self._frame_plain(h(TEXT, {"children": "a" * 20, "textWrap": "truncate-start"}), 6)
        assert lines == ["…" + "a" * 5]

    def test_textwrap_truncate_start_fits_no_ellipsis(self):
        """textWrap='truncate-start'：内容未超宽 → 原样单行（无省略号）。"""
        lines = self._frame_plain(h(TEXT, {"children": "abc", "textWrap": "truncate-start"}), 10)
        assert lines == ["abc"]

    def test_textwrap_truncate_middle_keeps_head_tail(self):
        """textWrap='truncate-middle'：保留头尾，中间省略号（react-ink 语义）。"""
        # 宽度 6 → 头 (6-1)//2=2 + … + 尾 3
        lines = self._frame_plain(h(TEXT, {"children": "abcdefghij", "textWrap": "truncate-middle"}), 6)
        assert lines == ["ab…hij"]

    def test_textwrap_truncate_middle_odd_width(self):
        """textWrap='truncate-middle'：奇数宽度头部取 floor、尾部取 ceil。"""
        # 宽度 7 → 头 3 + … + 尾 3
        lines = self._frame_plain(h(TEXT, {"children": "abcdefghij", "textWrap": "truncate-middle"}), 7)
        assert lines == ["abc…hij"]

    def test_textwrap_truncate_middle_narrow_falls_back_to_end(self):
        """textWrap='truncate-middle'：宽度 <=3 回退末尾省略号（预算不足）。"""
        lines = self._frame_plain(h(TEXT, {"children": "abcdef", "textWrap": "truncate-middle"}), 3)
        assert lines == ["ab…"]

    def test_textwrap_truncate_middle_cjk_not_split(self):
        """textWrap='truncate-middle'：CJK 宽字符不拆分（宽度依据 wcswidth_simple）。"""
        # "你好世界" 宽 8 → 截断至 6：头 2 格("你") + … + 尾 3 格("界")，总宽 5<=6
        lines = self._frame_plain(h(TEXT, {"children": "你好世界", "textWrap": "truncate-middle"}), 6)
        assert lines == ["你…界"]

    def test_textwrap_truncate_start_cjk_not_split(self):
        """textWrap='truncate-start'：CJK 宽字符不拆分（保留尾部）。"""
        # "你好世界" 宽 8 → 截断至 5：尾 4 格("世界") + …，总宽 5<=5
        lines = self._frame_plain(h(TEXT, {"children": "你好世界", "textWrap": "truncate-start"}), 5)
        assert lines == ["…世界"]

    def test_textwrap_default_wrap_unchanged(self):
        """默认 textWrap='wrap' 行为不变（回归：超宽换行而非截断）。"""
        root, box = _render_and_layout(h(TEXT, {"children": "a" * 30}), 10)
        assert box.h == 3  # 换行为 3 行（不截断）
        lines = self._frame_plain(h(TEXT, {"children": "a" * 30}), 10)
        assert lines == ["a" * 10, "a" * 10, "a" * 10]
