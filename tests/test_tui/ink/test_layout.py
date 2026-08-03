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


class TestTextAlign:
    """完善 react ink — TEXT align（left/right/center 文本对齐）。"""

    def test_align_left_default(self):
        root = Reconciler().create_root()
        el = h(TEXT, {"children": "ab", "width": 5})
        r = Reconciler()
        r.render(root, el, 80, 24)
        frame = render_frame(root, 80)
        assert frame.lines[0].plain == "ab"

    def test_align_right(self):
        root = Reconciler().create_root()
        el = h(TEXT, {"children": "ab", "width": 5, "align": "right"})
        r = Reconciler()
        r.render(root, el, 80, 24)
        frame = render_frame(root, 80)
        assert frame.lines[0].plain == "   ab"

    def test_align_center(self):
        root = Reconciler().create_root()
        el = h(TEXT, {"children": "ab", "width": 5, "align": "center"})
        r = Reconciler()
        r.render(root, el, 80, 24)
        frame = render_frame(root, 80)
        assert frame.lines[0].plain == " ab"

    def test_align_multiline(self):
        """多行换行后每行各自对齐。"""
        root = Reconciler().create_root()
        el = h(TEXT, {"children": "abcdef", "width": 3, "align": "right"})
        r = Reconciler()
        r.render(root, el, 80, 24)
        frame = render_frame(root, 80)
        # "abcdef" 按 3 列换行 → ["abc", "def"]，每行右侧对齐
        assert frame.lines[0].plain == "abc"
        assert frame.lines[1].plain == "def"

    def test_align_cache_hit_keeps_identity(self):
        """同 align 跨帧命中缓存返回对齐行对象（diff 身份短路保持）。"""
        r = Reconciler()
        root = r.create_root()
        el = h(TEXT, {"children": "ab", "width": 5, "align": "right"})
        r.render(root, el, 80, 24)
        lines1 = root.child._wrapped_lines
        # 同元素再渲染一次（同 props → fiber 复用）
        r.render(root, el, 80, 24)
        lines2 = root.child._wrapped_lines
        assert lines1 is lines2, "同 align 跨帧应复用缓存行对象"

    def test_align_change_invalidates_cache(self):
        """align 变化触发缓存重算（align 入缓存键）。"""
        r = Reconciler()
        root = r.create_root()
        el = h(TEXT, {"children": "ab", "width": 5, "align": "right"})
        r.render(root, el, 80, 24)
        assert root.child._wrapped_lines[0].plain == "   ab"
        el2 = h(TEXT, {"children": "ab", "width": 5, "align": "left"})
        r.render(root, el2, 80, 24)
        assert root.child._wrapped_lines[0].plain == "ab"


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


class TestFlexDistributeRemainder:
    """方向1 — flex 余数分配修复：余数仅分配给权重 >0 的节点（非按 children 索引）。"""

    def _collect_texts(self, root):
        texts = []
        child = root.child.child
        while child:
            texts.append(child)
            child = child.sibling
        return texts

    def test_flexgrow_remainder_skips_zero_weight(self):
        """flexGrow 权重 [0,2,1]、remaining=5 → 余数 2 全部分给权重>0 节点（grow=2 得 3、grow=1 得 2）。

        修复前按 children 索引 ``i < remainder`` 分配：i=0（grow=0）错误得分 1；
        修复后余数仅按权重>0 节点序列计索引（grow=2、grow=1 依次得余数 1+1）。
        """
        root, box = _render_and_layout(
            h(BOX, {"height": 8},  # 内容 3 行 + remaining 5
              h(TEXT, {"children": "a", "flexGrow": 0}),
              h(TEXT, {"children": "b", "flexGrow": 2}),
              h(TEXT, {"children": "c", "flexGrow": 1})),
            80,
        )
        texts = self._collect_texts(root)
        # 内容各 1 行；remaining=5, total=3, per=1, remainder=2
        # → grow=0: 0；grow=2: 1*2+1=3；grow=1: 1*1+1=2
        assert [t.layout_box.h for t in texts] == [1, 4, 3]
        assert box.h == 8  # 总高度不变

    def test_flexshrink_remainder_skips_zero_weight(self):
        """flexShrink 权重 [0,2]、deficit=5 → 仅 shrink=2 节点缩减（shrink=0 不受影响）。

        修复前按 children 索引 ``i < remainder`` 分配：i=0（shrink=0）错误缩减 1 行；
        修复后余数仅按权重>0 节点序列计索引（shrink=2 节点独立获得全部余数）。
        """
        root, box = _render_and_layout(
            h(BOX, {"height": 4},  # 内容 9 行（5+4）→ deficit=5
              h(TEXT, {"children": "a" * 50, "flexShrink": 0}),   # 5 行，不参与
              h(TEXT, {"children": "b" * 40, "flexShrink": 2})),  # 4 行，全缩减
            10,
        )
        texts = self._collect_texts(root)
        # deficit=5, total=2, per=2, remainder=1 → shrink=2 节点 reduce=2*2+1=5 → 4-5 → clamp 1
        assert texts[0].layout_box.h == 5   # shrink=0 不变
        assert texts[1].layout_box.h == 1   # shrink=2 全缩减至 1 行
        assert box.h == 4

    def test_flexshrink_reflows_grandchildren(self):
        """flexShrink 修改直接子节点高度后孙节点 y 递归重排（孙节点跟随新 y）。

        父 BOX(height=5) + 两个 shrink=1 的子容器（各 3 行内容）：
        deficit=1, shrink=[1,1], per=0, remainder=1 → A 缩减 1 行（y=0）、
        B 不缩减但 y 重排 3→2——B 内部孙节点 y 须同步重排（修复前孙节点 y
        保持 shrink 前的 [3,4,5] 陈旧值）。
        """
        root, box = _render_and_layout(
            h(BOX, {"height": 5},
              h(BOX, {"flexShrink": 1}, h(TEXT, {"children": "a" * 30})),
              h(BOX, {"flexShrink": 1}, h(TEXT, {"children": "b" * 30}))),
            10,
        )
        # 直接子容器
        children = []
        child = root.child.child
        while child:
            children.append(child)
            child = child.sibling
        assert len(children) == 2
        # A 缩减 1 行 → h=2；B 保持 h=3 但 y 重排到 2
        assert children[0].layout_box.h == 2
        assert children[0].layout_box.y == 0
        assert children[1].layout_box.h == 3
        assert children[1].layout_box.y == 2
        # 孙节点（B 内部 TEXT）y 递归重排：跟随 B.y=2（修复前陈旧为 3）
        b_text = children[1].child
        assert b_text.layout_box.y == 2
        assert b_text.layout_box.h == 3


class TestRowMargin:
    """方向1 — row 分支最后一个子节点不计 margin（与 column 一致）。"""

    def test_row_last_child_margin_not_counted(self):
        """row 三子节点 margin=1：内部宽度 = 3 + 2*1 = 5（最后 margin 不计）。"""
        root, box = _render_and_layout(
            h(BOX, {"flexDirection": "row", "margin": 1},
              h(TEXT, {"children": "a"}),
              h(TEXT, {"children": "b"}),
              h(TEXT, {"children": "c"})),
            80,
        )
        # 内容宽 3 + 两次 margin（1+1）= 5；修复前无条件累加 3 次 margin → 6
        assert box.w == 5


class TestRowFillFalseWidth:
    """方向1 步骤1.7 — row 方向 fill=False 子节点宽度（内容自适应）+ 剩余宽度 0 高度。"""

    def _collect_children(self, root):
        children = []
        child = root.child.child
        while child:
            children.append(child)
            child = child.sibling
        return children

    def test_row_fill_false_width_regression(self):
        """row 内 BOX 子节点（无显式 width）宽度为内容宽（非剩余宽）。

        修复前 column 分支忽略 fill 恒填满剩余行宽（row 内 BOX 错误占满
        avail_w）；修复后 fill=False 内容自适应（BOX 宽 = 内容 + padding）。
        """
        root, box = _render_and_layout(
            h(BOX, {"flexDirection": "row"},
              h(BOX, {"padding": 1}, h(TEXT, {"children": "ab"})),
              h(TEXT, {"children": "c"})),
            80,
        )
        children = self._collect_children(root)
        # BOX：padding 1 + 内容 2 + padding 1 = 宽 4（修复前填满剩余 80）
        assert children[0].layout_box.w == 4, (
            f"row 内 BOX 子节点应内容自适应宽 4，实际 {children[0].layout_box.w}"
        )
        # row 总宽 = 4 + 1 = 5（非修复前占满 80）
        assert box.w == 5, f"row 总宽应为 5（内容宽），实际 {box.w}"

    def test_row_remaining_zero_height_regression(self):
        """row 内剩余宽度 0 子节点高度为 0（row_h 不虚增）。

        修复前 fill=False 且 width=0 时 wrap_runs_by_width(runs, 0) 返回单行
        → h=1（零宽仍占 1 行高度）；修复后零宽非 fill 子节点不占位。
        """
        root, box = _render_and_layout(
            h(BOX, {"flexDirection": "row"},
              h(TEXT, {"children": "a" * 10}),
              h(TEXT, {"children": "b"})),
            10,
        )
        children = self._collect_children(root)
        assert children[0].layout_box.w == 10  # 占满
        assert children[1].layout_box.w == 0  # 剩余 0 → 宽 0
        assert children[1].layout_box.h == 0, (
            f"零宽非 fill 子节点高度应为 0，实际 {children[1].layout_box.h}"
        )
        assert box.h == 1  # row_h 不虚增

    def test_row_zero_width_spacer_no_height_regression(self):
        """row 内 SPACER 显式 width=0 → 高度 0（不虚增 row_h）。

        修复前 SPACER width=0 时 height 仍按 prop（如 5）参与 row_h 累加；
        修复后零宽 SPACER 不占位。
        """
        root, box = _render_and_layout(
            h(BOX, {"flexDirection": "row"},
              h(TEXT, {"children": "a" * 10}),
              h(SPACER, {"width": 0, "height": 5})),
            10,
        )
        children = self._collect_children(root)
        assert children[1].layout_box.w == 0
        assert children[1].layout_box.h == 0, (
            f"零宽 SPACER 高度应为 0，实际 {children[1].layout_box.h}"
        )
        assert box.h == 1

    def test_row_box_inner_text_content_width_regression(self):
        """row 内 BOX 内部 TEXT 以内容自适应测量（fill 沿树传播）。

        修复前 BOX 内 TEXT fill=True 填满内部宽度（BOX 恒占满剩余宽）；
        修复后 fill=False 沿树传播 → TEXT 内容宽决定 BOX 宽。
        """
        root, box = _render_and_layout(
            h(BOX, {"flexDirection": "row"},
              h(BOX, None, h(TEXT, {"children": "xy"})),
              h(TEXT, {"children": "z"})),
            80,
        )
        children = self._collect_children(root)
        # BOX 内容 TEXT 宽 2 → BOX 宽 2；row 总宽 3
        assert children[0].layout_box.w == 2, (
            f"row 内 BOX 内部 TEXT 内容自适应 → BOX 宽 2，实际 {children[0].layout_box.w}"
        )
        assert box.w == 3


class TestEmptyTextHeight:
    """方向1 — 空 TEXT 高度恒 ≥1 修复（空文本 h=0，不再产生空行占位）。"""

    def test_empty_text_height_zero(self):
        """空 TEXT（children=""）→ LayoutBox.h == 0（修复前恒 1）。"""
        root, box = _render_and_layout(h(TEXT, {"children": ""}), 80)
        assert box.h == 0

    def test_empty_text_styled_height_zero(self):
        """空 styled 列表 TEXT（styled=[]）→ h == 0。"""
        from src.tui.ink.output import StyledRun
        root, box = _render_and_layout(
            h(TEXT, {"styled": []}), 80
        )
        assert box.h == 0

    def test_nonempty_text_height_unchanged(self):
        """非空 TEXT 高度不变（回归：h == 换行行数）。"""
        root, box = _render_and_layout(h(TEXT, {"children": "abc"}), 80)
        assert box.h == 1
        root, box = _render_and_layout(h(TEXT, {"children": "a" * 30}), 10)
        assert box.h == 3


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


class TestJustifyContentAlignItems:
    """方向3 — column justifyContent 与 row alignItems（已实现）。"""

    def _collect_texts(self, root):
        texts = []
        child = root.child.child
        while child:
            texts.append(child)
            child = child.sibling
        return texts

    def test_column_justify_center(self):
        """column+center：剩余空间 extra=2 → 所有子节点 y += 1。"""
        root, box = _render_and_layout(
            h(BOX, {"height": 4, "justifyContent": "center"},
              h(TEXT, {"children": "a"}),
              h(TEXT, {"children": "b"})),
            80,
        )
        texts = self._collect_texts(root)
        assert box.h == 4
        assert texts[0].layout_box.y == 1  # 0 + extra//2 (2//2=1)
        assert texts[1].layout_box.y == 2  # 1 + 1

    def test_column_justify_flex_end(self):
        """column+flex-end：剩余空间 extra=2 → 所有子节点 y += 2。"""
        root, box = _render_and_layout(
            h(BOX, {"height": 4, "justifyContent": "flex-end"},
              h(TEXT, {"children": "a"}),
              h(TEXT, {"children": "b"})),
            80,
        )
        texts = self._collect_texts(root)
        assert box.h == 4
        assert texts[0].layout_box.y == 2
        assert texts[1].layout_box.y == 3

    def test_column_justify_flex_start_default(self):
        """column 默认 flex-start：无偏移（回归）。"""
        root, box = _render_and_layout(
            h(BOX, {"height": 4},
              h(TEXT, {"children": "a"}),
              h(TEXT, {"children": "b"})),
            80,
        )
        texts = self._collect_texts(root)
        assert texts[0].layout_box.y == 0
        assert texts[1].layout_box.y == 1

    def test_column_justify_no_extra_no_offset(self):
        """column+center 无剩余空间（extra=0）→ 无偏移。"""
        root, box = _render_and_layout(
            h(BOX, {"justifyContent": "center"},
              h(TEXT, {"children": "a"}),
              h(TEXT, {"children": "b"})),
            80,
        )
        texts = self._collect_texts(root)
        assert box.h == 2
        assert texts[0].layout_box.y == 0
        assert texts[1].layout_box.y == 1

    def test_column_justify_with_flexgrow_no_offset(self):
        """flexGrow 分尽余数后 justify 无偏移（grow 消费剩余空间）。"""
        root, box = _render_and_layout(
            h(BOX, {"height": 6, "justifyContent": "center"},
              h(TEXT, {"children": "a", "flexGrow": 1}),
              h(TEXT, {"children": "b", "flexGrow": 1})),
            80,
        )
        texts = self._collect_texts(root)
        # 内容 2 行 + remaining 4 → grow 分尽（每子 +2）→ 子高 [3,3]，extra=0
        assert box.h == 6
        assert [t.layout_box.h for t in texts] == [3, 3]
        assert texts[0].layout_box.y == 0  # grow 已消费全部剩余 → justify 无偏移
        assert texts[1].layout_box.y == 3

    def test_row_align_center(self):
        """row+center：子节点 y += (row_h - cbox.h)//2。"""
        root, box = _render_and_layout(
            h(BOX, {"flexDirection": "row", "alignItems": "center"},
              h(TEXT, {"children": "ab"}),
              h(BOX, {"height": 3}, h(TEXT, {"children": "c"}))),
            80,
        )
        # 第一个 TEXT 高 1，第二个 BOX 高 3 → row_h=3 → text0.y += (3-1)//2=1
        texts = []
        child = root.child.child
        while child:
            texts.append(child)
            child = child.sibling
        assert texts[0].layout_box.y == 1
        assert texts[1].layout_box.y == 0

    def test_row_align_flex_end(self):
        """row+flex-end：子节点 y += (row_h - cbox.h)。"""
        root, box = _render_and_layout(
            h(BOX, {"flexDirection": "row", "alignItems": "flex-end"},
              h(TEXT, {"children": "ab"}),
              h(BOX, {"height": 3}, h(TEXT, {"children": "c"}))),
            80,
        )
        texts = []
        child = root.child.child
        while child:
            texts.append(child)
            child = child.sibling
        assert texts[0].layout_box.y == 2  # (3-1)
        assert texts[1].layout_box.y == 0

    def test_row_align_stretch_default(self):
        """row 默认 stretch：无偏移（回归）。"""
        root, box = _render_and_layout(
            h(BOX, {"flexDirection": "row"},
              h(TEXT, {"children": "ab"}),
              h(BOX, {"height": 3}, h(TEXT, {"children": "c"}))),
            80,
        )
        texts = []
        child = root.child.child
        while child:
            texts.append(child)
            child = child.sibling
        assert texts[0].layout_box.y == 0
        assert texts[1].layout_box.y == 0


class TestRowFlexboxComplete:
    """方向1（完善 flexbox）— row flexGrow / row justifyContent / column alignItems。"""

    def _collect_texts(self, root):
        texts = []
        child = root.child.child
        while child:
            texts.append(child)
            child = child.sibling
        return texts

    # ── row flexGrow（横向主轴 grow） ──

    def test_row_flexgrow_equal(self):
        """row width=10 + 两子 flexGrow=1 → 每子 +3（extra 6/2），x 重排 0/5。"""
        root, box = _render_and_layout(
            h(BOX, {"flexDirection": "row", "width": 10},
              h(TEXT, {"children": "ab", "flexGrow": 1}),
              h(TEXT, {"children": "cd", "flexGrow": 1})),
            80,
        )
        texts = self._collect_texts(root)
        assert box.w == 10
        assert [t.layout_box.w for t in texts] == [5, 5]
        assert [t.layout_box.x for t in texts] == [0, 5]

    def test_row_flexgrow_weighted(self):
        """row flexGrow=2/1 → 宽度分配 6/4（2:1）。"""
        root, box = _render_and_layout(
            h(BOX, {"flexDirection": "row", "width": 10},
              h(TEXT, {"children": "ab", "flexGrow": 2}),
              h(TEXT, {"children": "cd", "flexGrow": 1})),
            80,
        )
        texts = self._collect_texts(root)
        assert [t.layout_box.w for t in texts] == [6, 4]
        assert [t.layout_box.x for t in texts] == [0, 6]

    def test_row_flexgrow_no_extra_unchanged(self):
        """无富余宽度（内容占满）→ flexGrow 不改变宽度。"""
        root, box = _render_and_layout(
            h(BOX, {"flexDirection": "row", "width": 4},
              h(TEXT, {"children": "ab", "flexGrow": 1}),
              h(TEXT, {"children": "cd", "flexGrow": 1})),
            80,
        )
        texts = self._collect_texts(root)
        assert [t.layout_box.w for t in texts] == [2, 2]

    # ── row justifyContent ──

    def test_row_justify_center(self):
        """row+center：extra=6 → 每子 x += 3。"""
        root, box = _render_and_layout(
            h(BOX, {"flexDirection": "row", "width": 10, "justifyContent": "center"},
              h(TEXT, {"children": "ab"}),
              h(TEXT, {"children": "cd"})),
            80,
        )
        texts = self._collect_texts(root)
        assert [t.layout_box.x for t in texts] == [3, 5]

    def test_row_justify_flex_end(self):
        """row+flex-end：extra=6 → 每子 x += 6。"""
        root, box = _render_and_layout(
            h(BOX, {"flexDirection": "row", "width": 10, "justifyContent": "flex-end"},
              h(TEXT, {"children": "ab"}),
              h(TEXT, {"children": "cd"})),
            80,
        )
        texts = self._collect_texts(root)
        assert [t.layout_box.x for t in texts] == [6, 8]

    def test_row_justify_space_between(self):
        """row+space-between：三子 "ab/cd/ef" extra=4 → 首左末右等间隔。"""
        root, box = _render_and_layout(
            h(BOX, {"flexDirection": "row", "width": 10, "justifyContent": "space-between"},
              h(TEXT, {"children": "ab"}),
              h(TEXT, {"children": "cd"}),
              h(TEXT, {"children": "ef"})),
            80,
        )
        texts = self._collect_texts(root)
        assert [t.layout_box.x for t in texts] == [0, 4, 8]

    def test_row_justify_space_evenly(self):
        """row+space-evenly：三子 extra=4 slots=4 per=1 → x = 1/4/7。"""
        root, box = _render_and_layout(
            h(BOX, {"flexDirection": "row", "width": 10, "justifyContent": "space-evenly"},
              h(TEXT, {"children": "ab"}),
              h(TEXT, {"children": "cd"}),
              h(TEXT, {"children": "ef"})),
            80,
        )
        texts = self._collect_texts(root)
        assert [t.layout_box.x for t in texts] == [1, 4, 7]

    def test_row_justify_space_around(self):
        """row+space-around：三子 "ab/cd/ef" width=12 extra=6 per=1 → x = 1/5/9。"""
        root, box = _render_and_layout(
            h(BOX, {"flexDirection": "row", "width": 12, "justifyContent": "space-around"},
              h(TEXT, {"children": "ab"}),
              h(TEXT, {"children": "cd"}),
              h(TEXT, {"children": "ef"})),
            80,
        )
        texts = self._collect_texts(root)
        assert [t.layout_box.x for t in texts] == [1, 5, 9]

    def test_row_justify_flex_start_default(self):
        """row 默认 flex-start：无偏移（回归）。"""
        root, box = _render_and_layout(
            h(BOX, {"flexDirection": "row", "width": 10},
              h(TEXT, {"children": "ab"}),
              h(TEXT, {"children": "cd"})),
            80,
        )
        texts = self._collect_texts(root)
        assert [t.layout_box.x for t in texts] == [0, 2]

    # ── column alignItems（横轴对齐） ──

    def test_column_align_center(self):
        """column+center：子节点按自然宽度测量并横向居中。"""
        root, box = _render_and_layout(
            h(BOX, {"width": 10, "alignItems": "center"},
              h(TEXT, {"children": "ab"}),
              h(TEXT, {"children": "cd"})),
            80,
        )
        texts = self._collect_texts(root)
        assert box.w == 10
        assert [t.layout_box.w for t in texts] == [2, 2]  # 自然宽度（非填充）
        assert [t.layout_box.x for t in texts] == [4, 4]  # (10-2)//2

    def test_column_align_flex_end(self):
        """column+flex-end：子节点靠右对齐。"""
        root, box = _render_and_layout(
            h(BOX, {"width": 10, "alignItems": "flex-end"},
              h(TEXT, {"children": "ab"}),
              h(TEXT, {"children": "cd"})),
            80,
        )
        texts = self._collect_texts(root)
        assert [t.layout_box.x for t in texts] == [8, 8]  # 10-2

    def test_column_align_stretch_default(self):
        """column 默认 stretch：子节点填充容器宽（回归）。"""
        root, box = _render_and_layout(
            h(BOX, {"width": 10},
              h(TEXT, {"children": "ab"})),
            80,
        )
        text = root.child.child
        assert text.layout_box.w == 10
        assert text.layout_box.x == 0


class TestFragment:
    """方向1（完善 ink）— Fragment 透明分组容器：子节点流入父容器布局。"""

    def test_fragment_flattens_into_parent(self):
        """Fragment 包裹两个 TEXT → 与直接子节点同布局（无额外 box）。"""
        root, box = _render_and_layout(
            h(BOX, {"width": 80},
              h("fragment", None,
                h(TEXT, {"children": "a"}),
                h(TEXT, {"children": "b"}))),
            80,
        )
        # fiber 树仍含 fragment 节点；layout_children 布局时扁平化——
        # fragment 的 TEXT 子节点直接以父容器坐标布局（y=0/1，无 box 偏移）
        frag = root.child.child
        assert frag.type == "fragment"
        first = frag.child
        assert first.type == "text"
        assert first.layout_box.y == 0
        second = first.sibling
        assert second.type == "text"
        assert second.layout_box.y == 1
        assert box.h == 2

    def test_fragment_nested_flattens(self):
        """嵌套 Fragment 递归扁平化。"""
        root, box = _render_and_layout(
            h(BOX, {"width": 80},
              h("fragment", None,
                h("fragment", None,
                  h(TEXT, {"children": "a"}),
                  h(TEXT, {"children": "b"})))),
            80,
        )
        outer = root.child.child
        assert outer.type == "fragment"
        inner = outer.child
        assert inner.type == "fragment"
        first = inner.child
        assert first.type == "text"
        assert first.layout_box.y == 0
        assert first.sibling.type == "text"
        assert box.h == 2

    def test_fragment_paints_children(self):
        """Fragment 子节点经 render_frame 正确渲染。"""
        root, _ = _render_and_layout(
            h(BOX, {"width": 80},
              h("fragment", None,
                h(TEXT, {"children": "hi"})),
              h(TEXT, {"children": "yo"})),
            80,
        )
        frame = render_frame(root, 80)
        assert [line.plain for line in frame.lines] == ["hi", "yo"]


class TestMalformedWidthFallback:
    """方向1 步骤3 — width 畸形兜底（_resolve_width 收敛，4 处 int(explicit_w) 统一）。"""

    def test_text_malformed_width_no_crash_regression(self):
        """TEXT width="abc"/对象 → 不抛异常（回退 avail）；正常 int 不变。"""
        root, box = _render_and_layout(h(TEXT, {"children": "abc", "width": "abc"}), 80)
        assert box.w == 80, f"畸形 width 应回退 avail，实际 {box.w}"
        root, box = _render_and_layout(h(TEXT, {"children": "abc", "width": object()}), 80)
        assert box.w == 80
        root, box = _render_and_layout(h(TEXT, {"children": "abc", "width": 5}), 80)
        assert box.w == 5  # 正常 int 行为不变

    def test_spacer_malformed_width_no_crash_regression(self):
        """SPACER width 畸形 → 回退 avail。"""
        root, box = _render_and_layout(h(SPACER, {"width": "abc"}), 80)
        assert box.w == 80

    def test_row_malformed_width_no_crash_regression(self):
        """row 容器 width 畸形 → 回退 avail。"""
        root, box = _render_and_layout(
            h(BOX, {"flexDirection": "row", "width": "abc"},
              h(TEXT, {"children": "ab"})),
            80,
        )
        assert box.w == 80

    def test_column_malformed_width_no_crash_regression(self):
        """column 容器 width 畸形 → 回退 avail。"""
        root, box = _render_and_layout(
            h(BOX, {"width": object()}, h(TEXT, {"children": "x"})),
            80,
        )
        assert box.w == 80

    def test_resolve_width_has_callers_regression(self):
        """_resolve_width 由死代码变为真源（4 处 int(explicit_w) 收敛后有调用方）。"""
        import inspect
        import src.tui.ink.layout as layout_mod
        src = inspect.getsource(layout_mod._measure)
        assert "width = _resolve_width(fiber, avail_w)" in src, (
            "_measure 应调用 _resolve_width（收敛真源）"
        )
        assert "max(0, int(explicit_w))" not in src, (
            "int(explicit_w) 直接解析应全部收敛至 _resolve_width"
        )


class TestAlignSelf:
    """方向3 — alignSelf 子级对齐覆盖（column 横轴 / row 纵轴）。"""

    def _collect_texts(self, root):
        out = []

        def walk(f):
            f2 = f
            while f2 is not None:
                if f2.is_host and f2.type == "text":
                    out.append(f2)
                walk(f2.child)
                f2 = f2.sibling

        walk(root)
        return out

    def test_column_align_self_center(self):
        """column+alignSelf:center：子按内容宽度测量并居中（父 stretch 不覆盖）。"""
        root, box = _render_and_layout(
            h(BOX, {"width": 10},
              h(TEXT, {"children": "ab", "alignSelf": "center"})),
            80,
        )
        texts = self._collect_texts(root)
        # 内容宽度（非填充）：w=2，居中 (10-2)//2=4
        assert texts[0].layout_box.w == 2
        assert texts[0].layout_box.x == 4

    def test_column_align_self_flex_end(self):
        """column+alignSelf:flex-end：子靠右（父 stretch 不覆盖）。"""
        root, box = _render_and_layout(
            h(BOX, {"width": 10},
              h(TEXT, {"children": "ab", "alignSelf": "flex-end"})),
            80,
        )
        texts = self._collect_texts(root)
        assert texts[0].layout_box.x == 8  # 10-2

    def test_column_align_self_mixed(self):
        """多个子各自 alignSelf 独立生效（center / flex-end / 默认 stretch）。"""
        root, box = _render_and_layout(
            h(BOX, {"width": 12},
              h(TEXT, {"children": "a", "alignSelf": "center"}),
              h(TEXT, {"children": "bb", "alignSelf": "flex-end"}),
              h(TEXT, {"children": "ccc"})),
            80,
        )
        texts = self._collect_texts(root)
        assert texts[0].layout_box.x == (12 - 1) // 2  # center
        assert texts[1].layout_box.x == 12 - 2          # flex-end
        assert texts[2].layout_box.w == 12              # 默认 stretch 填充

    def test_row_align_self_override(self):
        """row+alignSelf:center：子 y 在行高内居中（父 stretch 无偏移）。"""
        root, box = _render_and_layout(
            h(BOX, {"flexDirection": "row", "height": 4},
              h(TEXT, {"children": "a"}),
              h(TEXT, {"children": "b", "alignSelf": "flex-end"})),
            80,
        )
        # 行高 = 内容行高（子 h 均 1 → row_h=1），alignSelf 无可用偏移量——
        # 与既有 row alignItems 语义一致（相对行高而非容器高）
        texts = self._collect_texts(root)
        assert texts[1].layout_box.y == 0


class TestPaddingAxes:
    """方向3 — paddingX/paddingY 横向/纵向独立内边距（React Ink 语义）。"""

    def test_padding_xy_axis_layout(self):
        """paddingX=2/paddingY=1：横向 2 列、纵向 1 行内边距。"""
        root, box = _render_and_layout(
            h(BOX, {"paddingX": 2, "paddingY": 1, "width": 12},
              h(TEXT, {"children": "ab"})),
            40,
        )
        assert box.w == 12
        assert box.h == 3  # 1 行内容 + 2 行纵向内边距
        child = root.child.child
        assert child.layout_box.x == 2  # 横向内边距偏移
        assert child.layout_box.y == 1  # 纵向内边距偏移

    def test_padding_x_overrides_padding(self):
        """padding=1 + paddingX=2：横向用 2、纵向回退 1（React Ink 覆盖语义）。"""
        root, box = _render_and_layout(
            h(BOX, {"padding": 1, "paddingX": 2, "width": 8},
              h(TEXT, {"children": "ab"})),
            40,
        )
        assert box.h == 3  # 纵向 padding=1 → 内容 1 + 上下 2
        child = root.child.child
        assert child.layout_box.x == 2  # 横向 paddingX=2
        assert child.layout_box.y == 1  # 纵向 padding=1

    def test_padding_y_overrides_padding(self):
        """padding=1 + paddingY=3：纵向用 3、横向回退 1。"""
        root, box = _render_and_layout(
            h(BOX, {"padding": 1, "paddingY": 3, "width": 8},
              h(TEXT, {"children": "ab"})),
            40,
        )
        assert box.h == 7  # 内容 1 + 上下 3+3
        child = root.child.child
        assert child.layout_box.x == 1  # 横向 padding=1
        assert child.layout_box.y == 3  # 纵向 paddingY=3

    def test_padding_xy_malformed_fallback(self):
        """paddingX/paddingY 畸形值 → 兜底 0（不抛异常）。"""
        root, box = _render_and_layout(
            h(BOX, {"paddingX": object(), "paddingY": object(), "width": 8},
              h(TEXT, {"children": "ab"})),
            40,
        )
        child = root.child.child
        assert child.layout_box.x == 0
        assert child.layout_box.y == 0


class TestTranslateSubtreeMultiChild:
    """BUG-14 — _translate_subtree_x/y 遍历后代 sibling 链（嵌套多子容器不错位）。

    修复前仅递归 ``fiber.child``（首子链），嵌套容器内第 2+ 个子节点
    （child 的 sibling）停留在旧坐标 → alignItems/alignSelf/探针复用偏移后
    嵌套多子容器文本/边框错位。
    """

    def test_align_center_nested_multi_child(self):
        """column alignItems:center 嵌套 BOX 含 2 个 TEXT → 两文本均在边框内。"""
        from src.tui.ink import strip_ansi

        r = Reconciler()
        root = r.create_root()
        el = h(BOX, {"width": 10, "alignItems": "center"},
               h(BOX, {"border": 1},
                 [h(TEXT, {"children": "a"}), h(TEXT, {"children": "b"})]))
        r.render(root, el, 80, 24)
        frame = render_frame(root, 80)
        # 两文本都应在边框内（渲染无错位）
        for line in frame.lines:
            plain = strip_ansi(line.render())
            # 边框外不应出现 b（修复前 b 画在左边框外）
            assert "b │" not in plain or "│b" in plain, f"文本 b 应位于边框内: {plain!r}"
        # 布局坐标断言
        inner_box = root.child.child
        texts = []
        t = inner_box.child
        while t:
            texts.append(t)
            t = t.sibling
        assert len(texts) == 2
        assert texts[0].layout_box.x == texts[1].layout_box.x, (
            f"两文本 x 应一致（同在边框内）: {texts[0].layout_box.x} vs {texts[1].layout_box.x}"
        )

    def test_translate_y_multi_child(self):
        """探针复用路径（fill=False 列容器）嵌套多子后代整体平移（sibling 遍历）。"""
        r = Reconciler()
        root = r.create_root()
        # row 内 column BOX（fill=False 探针）：内部第 2 个子节点为嵌套 BOX
        # （含 c/d 两个文本），主循环平移该嵌套 BOX +1——其内部 c 和 d 均应
        # 随动（修复前仅首子链 c 平移、d 停留在探针 y 重叠基准）
        el = h(BOX, {"flexDirection": "row", "width": 30},
               h(BOX, {"flexDirection": "column", "border": 1},
                 [h(TEXT, {"children": "a"}),
                  h(BOX, {"flexDirection": "column", "border": 1},
                    [h(TEXT, {"children": "c"}), h(TEXT, {"children": "d"})])]))
        r.render(root, el, 80, 24)
        col = root.child.child
        texts = []
        t = col.child.sibling.child  # 嵌套 BOX 的第一个子
        while t:
            texts.append(t)
            t = t.sibling
        assert len(texts) == 2
        # c 与 d 都应在嵌套 BOX 边框内（y 差 1 相对保持）
        assert texts[1].layout_box.y - texts[0].layout_box.y == 1, (
            f"c/d 相对位置应保持: c={texts[0].layout_box.y} d={texts[1].layout_box.y}"
        )
        # d 必须被平移（探针 delta=1 后嵌套 BOX 整体下移）——修复前 d 停留
        # 在探针 y（≈1，与 c 重叠）；修复后 d 在嵌套 BOX 边框内 y=4
        assert texts[1].layout_box.y >= 3, (
            f"d 应随嵌套 BOX 整体平移（sibling 遍历）: {texts[1].layout_box.y}"
        )
