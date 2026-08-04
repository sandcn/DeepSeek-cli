"""测试 ink/layout.py 高级布局能力 — 百分比尺寸 / flexWrap / 绝对定位。

覆盖：
  - 百分比 width/height/minWidth/maxWidth（相对可用尺寸解析）；
  - flexWrap="wrap"：基本换行 / 行间距 / 单超宽项截断 / gap；
  - position="absolute"：left/top 锚点 / right/bottom 锚点 / 显式尺寸 /
    relative 基准 / 不占正常流空间 / 覆盖顺序；
  - 绝对定位百分比尺寸与拉伸（left+right / top+bottom）。
"""

from __future__ import annotations

from src.tui.ink import h, BOX, TEXT
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.components import render_frame


def _boxes_and_frame(el, width=80, height=24):
    """渲染元素树，返回 (layout_boxes 列表, Frame)。"""
    r = Reconciler()
    root = r.create_root()
    r.render(root, el, width, height)
    boxes = []

    def _collect(fiber):
        f = fiber
        while f is not None:
            if f.is_host and f.layout_box is not None:
                b = f.layout_box
                boxes.append((
                    f.type,
                    f.props.get("children", ""),
                    (b.x, b.y, b.w, b.h),
                ))
            if f.child:
                _collect(f.child)
            f = f.sibling

    _collect(root)
    return boxes, render_frame(root, width)


def _box(boxes, children):
    """按 children 文本查找布局盒（首个匹配）。"""
    for ftype, ch, box in boxes:
        if ch == children:
            return box
    raise AssertionError(f"未找到 children={children!r} 的布局盒: {boxes}")


# ═══════════════════════════════════════════════════════════
# 百分比尺寸
# ═══════════════════════════════════════════════════════════


class TestPercentSize:
    def test_percent_width(self):
        boxes, _ = _boxes_and_frame(h(BOX, {"width": 20, "flexDirection": "column"}, [
            h(TEXT, {"children": "abcdef", "width": "50%"}),
        ]))
        assert _box(boxes, "abcdef") == (0, 0, 10, 1)  # 50% × 20

    def test_percent_width_nested(self):
        """嵌套：内层 50% 相对父内宽。"""
        boxes, _ = _boxes_and_frame(h(BOX, {"width": 40, "padding": 4, "flexDirection": "column"}, [
            h(TEXT, {"children": "x", "width": "50%"}),
        ]))
        # 父内宽 = 40 - 8 = 32 → 50% = 16
        assert _box(boxes, "x") == (4, 4, 16, 1)

    def test_percent_height_relative_parent(self):
        """height="50%" 相对父显式高度。"""
        el = h(BOX, {"height": 10, "flexDirection": "column"}, [
            h(BOX, {"height": "50%", "flexDirection": "column"},
              h(TEXT, {"children": "inner"})),
        ])
        boxes, _ = _boxes_and_frame(el)
        # 子容器高度 = 50% × 10 = 5；inner TEXT 在其内
        target = [b for ft, ch, b in boxes if ft == "box" and b[3] == 5]
        assert target, f"应存在高度 5 的容器: {boxes}"
        inner = _box(boxes, "inner")
        assert inner[1] < 5  # inner 位于容器内

    def test_percent_min_max_width(self):
        boxes, _ = _boxes_and_frame(h(BOX, {"width": 20, "flexDirection": "column"}, [
            h(TEXT, {"children": "abc", "width": "10%", "minWidth": "50%"}),
            h(TEXT, {"children": "abcd", "width": "80%", "maxWidth": "25%"}),
        ]))
        # 第一个：10%×20=2 → min 50%×20=10 → 10
        assert _box(boxes, "abc") == (0, 0, 10, 1)
        # 第二个：80%×20=16 → max 25%×20=5 → 5
        assert _box(boxes, "abcd") == (0, 1, 5, 1)

    def test_malformed_percent(self):
        """畸形百分比（非数字）回退可用宽度。"""
        boxes, _ = _boxes_and_frame(h(BOX, {"width": 10, "flexDirection": "column"}, [
            h(TEXT, {"children": "abc", "width": "abc%"}),
        ]))
        assert _box(boxes, "abc") == (0, 0, 10, 1)


# ═══════════════════════════════════════════════════════════
# flexWrap
# ═══════════════════════════════════════════════════════════


class TestFlexWrap:
    def test_basic_wrap(self):
        el = h(BOX, {"flexDirection": "row", "flexWrap": "wrap", "width": 8, "gap": 1}, [
            h(TEXT, {"children": "aaa"}), h(TEXT, {"children": "bb"}),
            h(TEXT, {"children": "c"}), h(TEXT, {"children": "dddd"}),
        ])
        boxes, frame = _boxes_and_frame(el)
        assert _box(boxes, "aaa") == (0, 0, 3, 1)
        assert _box(boxes, "bb") == (4, 0, 2, 1)
        assert _box(boxes, "c") == (7, 0, 1, 1)
        # dddd 换到第二行（第一行宽 8-1=7：3+1+2+1+1=7 → dddd 放不下）
        assert _box(boxes, "dddd") == (0, 2, 4, 1)

    def test_wrap_row_gap(self):
        """行间距 = gap（换行后第二行 y 偏移 = 首行高 + gap）。"""
        el = h(BOX, {"flexDirection": "row", "flexWrap": "wrap", "width": 5, "gap": 2}, [
            h(TEXT, {"children": "aa"}), h(TEXT, {"children": "bb"}),
        ])
        boxes, _ = _boxes_and_frame(el)
        assert _box(boxes, "aa") == (0, 0, 2, 1)
        assert _box(boxes, "bb") == (0, 3, 2, 1)  # y = 1 + 2（gap）

    def test_single_overflow_item(self):
        """单个超宽项占一行（宽度截断到容器内宽）。"""
        el = h(BOX, {"flexDirection": "row", "flexWrap": "wrap", "width": 5}, [
            h(TEXT, {"children": "longword"}), h(TEXT, {"children": "a"}),
        ])
        boxes, _ = _boxes_and_frame(el)
        # longword 宽 8 > 5 → 截断为 5，独占一行
        assert _box(boxes, "longword") == (0, 0, 5, 2)  # 宽 5 内换行 2 行
        assert _box(boxes, "a") == (0, 2, 1, 1)  # 第二行

    def test_wrap_height_accumulates(self):
        """总高 = 各行累加 + 行间距。"""
        el = h(BOX, {"flexDirection": "row", "flexWrap": "wrap", "width": 4, "gap": 1}, [
            h(TEXT, {"children": "aa"}), h(TEXT, {"children": "bb"}),
            h(TEXT, {"children": "cc"}),
        ])
        boxes, frame = _boxes_and_frame(el)
        assert len(frame.lines) == 5  # 3 行内容 + 2 行间距

    def test_wrap_nested_translate(self):
        """换行重排后嵌套容器后代坐标正确（_translate_subtree_y）。"""
        el = h(BOX, {"flexDirection": "row", "flexWrap": "wrap", "width": 4, "gap": 1}, [
            h(BOX, {"flexDirection": "column"},
              h(TEXT, {"children": "a"}), h(TEXT, {"children": "b"})),
            h(TEXT, {"children": "ccc"}),
        ])
        boxes, _ = _boxes_and_frame(el)
        # 第一行：BOX（宽1 高2）→ 1+1(gap)=2；ccc 宽 3 → 2+3=5 > 4 换行
        assert _box(boxes, "a") == (0, 0, 1, 1)
        assert _box(boxes, "b") == (0, 1, 1, 1)
        # ccc 换到第二行（y = 2 + 1 gap = 3）
        assert _box(boxes, "ccc") == (0, 3, 3, 1)


# ═══════════════════════════════════════════════════════════
# 绝对定位
# ═══════════════════════════════════════════════════════════


class TestAbsolutePosition:
    def test_left_top(self):
        el = h(BOX, {"position": "relative", "width": 20, "height": 5, "border": 1}, [
            h(TEXT, {"children": "base"}),
            h(TEXT, {"children": "pop", "position": "absolute", "left": 2, "top": 1}),
        ])
        boxes, frame = _boxes_and_frame(el)
        # 内容区 x=1, y=1；left=2, top=1 → x=3, y=2
        assert _box(boxes, "pop") == (3, 2, 3, 1)
        # base 仍在正常流（y=1 内容区，fill 填充内容区宽 18）
        assert _box(boxes, "base") == (1, 1, 18, 1)

    def test_right_bottom(self):
        el = h(BOX, {"position": "relative", "width": 16, "height": 4, "border": 1}, [
            h(BOX, {"position": "absolute", "right": 0, "bottom": 0, "border": 1},
              h(TEXT, {"children": "ok"})),
        ])
        boxes, _ = _boxes_and_frame(el)
        # 内容区 x=1, w=14；pop 宽 4 → x = 1+14-0-4 = 11；内容区 y=1, h=2；
        # pop 高 3 → y = 1+2-0-3 = 0
        ok = [b for ft, ch, b in boxes if ft == "text" and ch == "ok"]
        assert ok and ok[0] == (12, 1, 2, 1)

    def test_explicit_width_height(self):
        el = h(BOX, {"position": "relative", "width": 20, "height": 10, "padding": 1}, [
            h(BOX, {"position": "absolute", "left": 3, "top": 2, "width": 8, "height": 4, "border": 1},
              h(TEXT, {"children": "in"})),
        ])
        boxes, _ = _boxes_and_frame(el)
        # left=3 → x=3；top=2 → y=2；width=8, height=4
        target = [b for ft, ch, b in boxes if ft == "box" and ch == "" and b[2:] == (8, 4)]
        assert target, f"应存在 8x4 的绝对定位容器: {boxes}"

    def test_no_flow_space(self):
        """绝对定位元素不占正常流空间（父高度不含其高度）。"""
        el = h(BOX, {"position": "relative", "width": 10, "flexDirection": "column"}, [
            h(TEXT, {"children": "line1"}),
            h(BOX, {"position": "absolute", "top": 0, "left": 0, "height": 5},
              h(TEXT, {"children": "overlay"})),
        ])
        boxes, frame = _boxes_and_frame(el)
        # 父容器高度 = 1（只有 line1 在正常流）
        assert len(frame.lines) == 1
        assert _box(boxes, "line1") == (0, 0, 10, 1)
        # overlay TEXT 在 y=0 覆盖 line1（不增加文档高度）
        assert _box(boxes, "overlay") == (0, 0, 7, 1)
        assert frame.lines[0].plain == "overlay"  # 覆盖

    def test_default_relative_base_is_root(self):
        """无 relative 祖先时以根为定位基准。"""
        el = h(BOX, {"flexDirection": "column"}, [
            h(TEXT, {"children": "a"}),
            h(BOX, {"position": "absolute", "top": 0, "left": 0},
              h(TEXT, {"children": "top"})),
        ])
        boxes, _ = _boxes_and_frame(el)
        assert _box(boxes, "top") == (0, 0, 3, 1)

    def test_nested_relative_base(self):
        """最近的 relative 祖先作为定位基准（嵌套）。"""
        el = h(BOX, {"width": 30, "flexDirection": "column"}, [
            h(BOX, {"position": "relative", "width": 10, "height": 4, "border": 1},
              h(TEXT, {"children": "inside"}),
              h(TEXT, {"children": "ov", "position": "absolute", "right": 0, "top": 0})),
        ])
        boxes, _ = _boxes_and_frame(el)
        # 基准容器内容区 x=1, w=8；ov 宽 2 → x = 1+8-0-2 = 7；y = 1
        assert _box(boxes, "ov") == (7, 1, 2, 1)

    def test_paint_overlay_order(self):
        """后声明的绝对定位元素覆盖先声明的（Z 顺序 = 声明顺序）。"""
        el = h(BOX, {"position": "relative", "width": 5, "height": 1}, [
            h(TEXT, {"children": "-----"}),
            h(TEXT, {"children": "X", "position": "absolute", "left": 2, "top": 0}),
        ])
        _, frame = _boxes_and_frame(el)
        assert frame.lines[0].plain == "--X--"

    def test_absolute_percent_size(self):
        """绝对定位百分比尺寸相对基准内容区。"""
        el = h(BOX, {"position": "relative", "width": 20, "height": 10, "border": 1}, [
            h(BOX, {"position": "absolute", "left": 0, "top": 0, "width": "50%", "height": "50%"},
              h(TEXT, {"children": "half"})),
        ])
        boxes, _ = _boxes_and_frame(el)
        # 内容区 18x8 → 50% = 9x4
        target = [b for ft, ch, b in boxes if ft == "box" and b[2:] == (9, 4)]
        assert target, f"应存在 9x4 的百分比容器: {boxes}"
