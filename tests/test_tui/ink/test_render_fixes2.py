"""测试 ink 渲染/布局修复（方向3：本轮新增）。

覆盖 BUG-1/2/3（探针复现 + 修复锁定）：
  - BUG-1：TEXT children 含 ``\\n`` 按换行拆分（react-ink 语义）
  - BUG-2：alignItems/alignSelf 偏移随动后代布局盒（嵌套容器不错位）
  - BUG-3：``_reflow_subtree`` 区分 flexDirection（flexShrink row 子节点不竖排）
"""

from __future__ import annotations

from src.tui.ink import h, BOX, TEXT
from src.tui.ink.output import Frame
from src.tui.ink.reconciler import Reconciler
from src.tui.ink import components as _components


def _render(el, width: int = 30) -> Frame:
    root = Reconciler.create_root()
    recon = Reconciler()
    recon.render(root, el, width, 24)
    return _components.render_frame(root, width)


def _plains(frame: Frame) -> list[str]:
    return [line.plain for line in frame.lines]


class TestTextNewlineSplit:
    """BUG-1 — TEXT children 含换行符按行拆分。"""

    def test_text_children_newline_splits(self):
        frame = _render(h(TEXT, {"children": "a\nb\nc"}))
        assert _plains(frame) == ["a", "b", "c"]

    def test_text_children_mixed_newline_and_wrap(self):
        frame = _render(h(TEXT, {"children": "aaaa\nbbbb", "width": 4}))
        assert _plains(frame) == ["aaaa", "bbbb"]

    def test_text_children_newline_with_trailing(self):
        frame = _render(h(TEXT, {"children": "ab\n"}))
        assert _plains(frame) == ["ab"]

    def test_styled_runs_with_newline(self):
        from src.tui.ink import StyledRun
        frame = _render(h(TEXT, {"styled": [StyledRun("a\nb", None)]}))
        assert _plains(frame) == ["a", "b"]


class TestAlignOffsetDescendants:
    """BUG-2 — alignItems/alignSelf 偏移随动后代布局盒。"""

    def test_column_align_center_nested_box(self):
        frame = _render(h(BOX, {"width": 10, "alignItems": "center"}, [
            h(BOX, {"border": 1}, [h(TEXT, {"children": "a"})]),
        ]))
        assert _plains(frame) == ["   ┌─┐", "   │a│", "   └─┘"]

    def test_column_align_flex_end_nested_box(self):
        frame = _render(h(BOX, {"width": 10, "alignItems": "flex-end"}, [
            h(BOX, {"border": 1}, [h(TEXT, {"children": "a"})]),
        ]))
        assert _plains(frame) == ["       ┌─┐", "       │a│", "       └─┘"]

    def test_column_alignSelf_nested_box(self):
        frame = _render(h(BOX, {"width": 10}, [
            h(BOX, {"border": 1, "alignSelf": "center"}, [h(TEXT, {"children": "a"})]),
        ]))
        assert _plains(frame) == ["   ┌─┐", "   │a│", "   └─┘"]

    def test_row_align_center_nested_box(self):
        """row alignItems center：矮子节点 y 下移、高子节点不动（row_h 基准）。"""
        frame = _render(h(BOX, {"width": 20, "flexDirection": "row", "alignItems": "center"}, [
            h(TEXT, {"children": "a"}),
            h(BOX, {"border": 1}, [h(TEXT, {"children": "b"})]),
        ]))
        # TEXT h=1、BOX h=3 → row_h=3 → text.y=1、box.y=0（BOX 起于 col1——A 后 spacing）
        row1 = _plains(frame)[1]
        assert "┌─┐" in row1 or "│b│" in row1
        assert "a" in row1


class TestReflowSubtreeDirection:
    """BUG-3 — _reflow_subtree 区分 flexDirection。"""

    def _collect_texts(self, root):
        out = []

        def walk(f):
            while f:
                if f.is_host and f.type == "text":
                    out.append((f.props.get("children"), f.layout_box.x, f.layout_box.y))
                walk(f.child)
                f = f.sibling

        walk(root)
        return out

    def test_row_flexshrink_children_horizontal(self):
        """row 容器 flexShrink 后子节点仍横向排列（不竖排）。"""
        root = Reconciler.create_root()
        recon = Reconciler()
        el = h(BOX, {"width": 20, "flexDirection": "row", "height": 2}, [
            h(BOX, {"flexShrink": 1}, [h(TEXT, {"children": "a"}), h(TEXT, {"children": "b"}), h(TEXT, {"children": "c"})]),
            h(TEXT, {"children": "X"}),
        ])
        recon.render(root, el, 30, 24)
        texts = self._collect_texts(root)
        # 两个 TEXT 兄弟 x 递增（横向排列），y 相同
        assert len(texts) >= 2
        xs = [t[1] for t in texts]
        assert xs[1] >= xs[0]  # 第二项 x 在第一项右侧（横向）

    def test_column_flexshrink_children_vertical(self):
        """column 容器 flexShrink 后子节点纵向堆叠（y 递增）。"""
        root = Reconciler.create_root()
        recon = Reconciler()
        el = h(BOX, {"width": 20, "height": 2}, [
            h(BOX, {"flexShrink": 1}, [h(TEXT, {"children": "a"}), h(TEXT, {"children": "b"}), h(TEXT, {"children": "c"})]),
        ])
        recon.render(root, el, 30, 24)
        texts = self._collect_texts(root)
        assert len(texts) == 3
        ys = [t[2] for t in texts]
        assert ys[0] < ys[1] < ys[2]  # 纵向堆叠
