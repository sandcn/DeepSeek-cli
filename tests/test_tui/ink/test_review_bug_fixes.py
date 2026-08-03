"""第九轮 review 发现的布局/调和 bug 修复测试。

覆盖：
  - BUG-15: flexGrow/justify 偏移/重排不平移后代（嵌套容器错位）
  - BUG-17: 零宽 row 子 TEXT 仍被绘制（溢出容器）
  - BUG-25: 双宽度函数不一致（BOM/软连字符等零宽字符）——见 test_screen.py
"""

from __future__ import annotations

from src.tui.ink.element import h, BOX, TEXT
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.components import render_frame
from src.tui.ink import strip_ansi


class TestFlexGrowJustifyTranslatesDescendants:
    """BUG-15 — flexGrow / justifyContent 偏移/重排不平移后代（嵌套容器错位）。"""

    def test_column_justify_center_nested_box(self):
        """column justifyContent:center 嵌套 BOX 后代随动（文本在边框内）。"""
        r = Reconciler()
        root = r.create_root()
        el = h(BOX, {"height": 6, "justifyContent": "center"},
               h(BOX, {"border": 1}, [h(TEXT, {"children": "a"})]))
        r.render(root, el, 80, 24)
        frame = render_frame(root, 80)
        inner = root.child.child
        text = inner.child
        # 嵌套 BOX 被 center 偏移，内部 TEXT 应随动（在边框内）
        assert text.layout_box.y == inner.layout_box.y + 1, (
            f"TEXT 应随嵌套 BOX 平移（边框内）: text.y={text.layout_box.y} box.y={inner.layout_box.y}"
        )

    def test_column_flexgrow_nested_box(self):
        """column flexGrow 嵌套多子容器后代重排（文本不压自身边框）。"""
        r = Reconciler()
        root = r.create_root()
        el = h(BOX, {"height": 8},
               [h(BOX, {"flexGrow": 1, "border": 1}, [h(TEXT, {"children": "a"})]),
                h(BOX, {"flexGrow": 1, "border": 1}, [h(TEXT, {"children": "b"})])])
        r.render(root, el, 80, 24)
        # 第二个嵌套 BOX 被 grow 重排到下方，内部 TEXT b 应随动
        second = root.child.child.sibling
        text_b = second.child
        assert text_b.layout_box.y == second.layout_box.y + 1, (
            f"TEXT b 应位于第二个 BOX 边框内（grow 重排后随动）: "
            f"text.y={text_b.layout_box.y} box.y={second.layout_box.y}"
        )

    def test_row_justify_space_between_nested_box(self):
        """row justifyContent:space-between 嵌套 BOX 后代随动。"""
        r = Reconciler()
        root = r.create_root()
        el = h(BOX, {"flexDirection": "row", "width": 20, "justifyContent": "space-between"},
               [h(BOX, {"border": 1}, [h(TEXT, {"children": "a"})]),
                h(BOX, {"border": 1}, [h(TEXT, {"children": "b"})])])
        r.render(root, el, 80, 24)
        second = root.child.child.sibling
        text_b = second.child
        # 第二个 BOX 被重排到右端，内部 TEXT b 应随动（在边框内）
        assert text_b.layout_box.x == second.layout_box.x + 1, (
            f"TEXT b 应位于第二个 BOX 边框内（space-between 重排后随动）: "
            f"text.x={text_b.layout_box.x} box.x={second.layout_box.x}"
        )


class TestZeroWidthTextNotPainted:
    """BUG-17 — 零宽/零高 TEXT 不绘制（row 剩余宽度 0 的子节点不溢出）。"""

    def test_zero_width_row_child_not_painted(self):
        """row 宽 3 内 "abc"+"def"：第二个 TEXT w=0 h=0 → 不绘制（只显示 abc）。"""
        r = Reconciler()
        root = r.create_root()
        el = h(BOX, {"flexDirection": "row", "width": 3},
               [h(TEXT, {"children": "abc"}), h(TEXT, {"children": "def"})])
        r.render(root, el, 80, 24)
        frame = render_frame(root, 80)
        plains = [strip_ansi(l.render()) for l in frame.lines]
        assert plains[0] == "abc", f"零宽子节点不应绘制（溢出）: {plains!r}"
        assert "def" not in "".join(plains), f"零宽子节点文本不应出现在画布: {plains!r}"
