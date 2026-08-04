"""测试 ink/widgets/layout.py — Row / Column / Center / Stack / Grid / ZStack 语义容器。

覆盖：
  - Row/Column 方向与 props 透传；
  - Center 双轴居中；
  - Stack/HStack/VStack 堆叠 + gap；
  - Grid 列分组 / 等宽填充 / 固定宽度 / 不足行补齐；
  - ZStack 层叠覆盖（绝对定位）与 props 透传。
"""

from __future__ import annotations

from src.tui.ink import h, BOX, TEXT
from src.tui.ink.widgets import (
    Row, Column, Center, Stack, HStack, VStack, Grid, ZStack,
)
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.components import render_frame


def _frame(el, width=80, height=24):
    r = Reconciler()
    root = r.create_root()
    r.render(root, el, width, height)
    return render_frame(root, width)


# ═══════════════════════════════════════════════════════════
# Row / Column
# ═══════════════════════════════════════════════════════════


class TestRowColumn:
    def test_row_horizontal(self):
        frame = _frame(h(Row, {"gap": 1}, h(TEXT, {"children": "a"}), h(TEXT, {"children": "b"})))
        assert [ln.plain for ln in frame.lines] == ["a b"]

    def test_column_vertical(self):
        frame = _frame(h(Column, {}, h(TEXT, {"children": "a"}), h(TEXT, {"children": "b"})))
        assert [ln.plain for ln in frame.lines] == ["a", "b"]

    def test_row_props_passthrough(self):
        """Row 透传非 children props（如 padding/border）。"""
        frame = _frame(h(Row, {"border": 1, "gap": 1}, h(TEXT, {"children": "ab"}), h(TEXT, {"children": "c"})))
        lines = [ln.plain for ln in frame.lines]
        assert lines[0] == "┌────┐"
        assert lines[1] == "│ab c│"
        assert lines[2] == "└────┘"

    def test_column_gap(self):
        frame = _frame(h(Column, {"gap": 2}, h(TEXT, {"children": "a"}), h(TEXT, {"children": "b"})))
        assert [ln.plain for ln in frame.lines] == ["a", "", "", "b"]


# ═══════════════════════════════════════════════════════════
# Center
# ═══════════════════════════════════════════════════════════


class TestCenter:
    def test_center_both_axes(self):
        frame = _frame(h(Center, {"width": 10, "height": 3}, h(TEXT, {"children": "x"})))
        lines = [ln.plain for ln in frame.lines]
        assert lines == ["", "    x", ""]

    def test_center_content_size(self):
        """无显式尺寸时容器占满可用宽度（内容居中）。"""
        frame = _frame(h(Center, {}, h(TEXT, {"children": "x"})))
        line = frame.lines[0].plain
        # 80 列宽，x 居中：前导空格 = (80-1)//2 = 39
        assert line == " " * 39 + "x"


# ═══════════════════════════════════════════════════════════
# Stack / HStack / VStack
# ═══════════════════════════════════════════════════════════


class TestStack:
    def test_stack_default_vertical(self):
        frame = _frame(h(Stack, {"gap": 1}, h(TEXT, {"children": "a"}), h(TEXT, {"children": "b"})))
        assert [ln.plain for ln in frame.lines] == ["a", "", "b"]

    def test_hstack_horizontal(self):
        frame = _frame(h(HStack, {"gap": 2}, h(TEXT, {"children": "a"}), h(TEXT, {"children": "b"})))
        assert [ln.plain for ln in frame.lines] == ["a  b"]

    def test_vstack_vertical(self):
        frame = _frame(h(VStack, {"gap": 1}, h(TEXT, {"children": "a"}), h(TEXT, {"children": "b"}), h(TEXT, {"children": "c"})))
        assert [ln.plain for ln in frame.lines] == ["a", "", "b", "", "c"]


# ═══════════════════════════════════════════════════════════
# Grid
# ═══════════════════════════════════════════════════════════


class TestGrid:
    def test_grid_equal_columns(self):
        """columns=3 近似等宽（flexGrow 权重分配，列宽差 ≤1）。"""
        el = h(Grid, {"columns": 3, "gap": 1},
               h(TEXT, {"children": "1"}), h(TEXT, {"children": "22"}), h(TEXT, {"children": "333"}))
        frame = _frame(el)
        line = frame.lines[0].plain
        # 三列近似等宽（78 - 2 gaps 分 3 列，每列 ~25-26）
        i1, i2, i3 = line.index("1"), line.index("22"), line.index("333")
        w1 = i2 - i1 - 1
        w2 = i3 - i2 - 2
        assert abs(w1 - w2) <= 1
        assert w1 >= 25

    def test_grid_fixed_width(self):
        """固定容器宽度下列等宽。"""
        el = h(Grid, {"columns": 2, "gap": 1, "width": 12},
               h(TEXT, {"children": "aa"}), h(TEXT, {"children": "b"}), h(TEXT, {"children": "ccc"}))
        frame = _frame(el)
        assert frame.lines[0].plain.startswith("aa")
        assert "b" in frame.lines[0].plain
        # 第二行 ccc 独占一行
        assert "ccc" in frame.lines[2].plain

    def test_grid_second_row(self):
        """超过列数的子节点换到下一行。"""
        el = h(Grid, {"columns": 2, "gap": 1, "width": 8},
               h(TEXT, {"children": "a"}), h(TEXT, {"children": "b"}),
               h(TEXT, {"children": "c"}))
        frame = _frame(el)
        lines = [ln.plain for ln in frame.lines]
        assert lines[0].startswith("a")
        assert lines[0].rstrip().endswith("b") or "b" in lines[0]
        assert "c" in lines[2]

    def test_grid_gap_row_spacing(self):
        el = h(Grid, {"columns": 2, "gap": 2, "width": 8},
               h(TEXT, {"children": "a"}), h(TEXT, {"children": "b"}),
               h(TEXT, {"children": "c"}))
        frame = _frame(el)
        # gap=2 → 列间距 2（列宽 3 → 'a  2空格 b'）；行间距 2 个空行
        assert frame.lines[0].plain == "a    b"
        assert [ln.plain for ln in frame.lines] == ["a    b", "", "", "c"]

    def test_grid_cell_flex_grow(self):
        """Grid 内 cell 可继续 flexGrow 扩展（嵌套容器等宽）。"""
        el = h(Grid, {"columns": 2, "gap": 1, "width": 10},
               h(BOX, {"border": 1}, h(TEXT, {"children": "x"})),
               h(TEXT, {"children": "y"}))
        frame = _frame(el)
        # cell1（BOX 边框）扩展 + cell2 y 占满 10 列
        line0 = frame.lines[0].plain
        assert line0.startswith("┌")
        assert "y" in line0
        assert "┐" in line0

    def test_grid_children_passthrough(self):
        """Grid 外层 props 透传（border/padding）。"""
        el = h(Grid, {"columns": 2, "gap": 1, "width": 8, "border": 1},
               h(TEXT, {"children": "a"}), h(TEXT, {"children": "b"}))
        frame = _frame(el)
        assert frame.lines[0].plain.startswith("┌")


# ═══════════════════════════════════════════════════════════
# ZStack
# ═══════════════════════════════════════════════════════════


class TestZStack:
    def test_overlay_covers(self):
        """后声明元素覆盖先声明元素。"""
        el = h(ZStack, {"width": 5, "height": 1},
               h(TEXT, {"children": "-----"}),
               h(TEXT, {"children": "X"}))
        frame = _frame(el)
        assert frame.lines[0].plain == "X----"

    def test_props_passthrough(self):
        """ZStack 透传 width/height/border。"""
        el = h(ZStack, {"width": 8, "height": 3, "border": 1},
               h(TEXT, {"children": "aaaa"}),
               h(TEXT, {"children": "XX"}))
        frame = _frame(el)
        lines = [ln.plain for ln in frame.lines]
        assert len(lines) == 3
        assert lines[0].startswith("┌")
        assert "XX" in lines[1]  # 覆盖
        assert lines[2].startswith("└")

    def test_box_overlay(self):
        """BOX 子节点层叠（边框覆盖）。"""
        el = h(ZStack, {"width": 6, "height": 3, "border": 1},
               h(BOX, {"border": 1}, h(TEXT, {"children": "aa"})),
               h(TEXT, {"children": "XX"}))
        frame = _frame(el)
        lines = [ln.plain for ln in frame.lines]
        assert "XX" in lines[1]  # XX 覆盖 aa 区域
