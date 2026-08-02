"""测试 ink 渲染修复（水平定位 / Line 行合并 / None 子级 / row width）。

覆盖本次「渲染错误修复」锁定：
  - T1 row-of-texts：row 内多个 TEXT 兄弟全部绘制（修复前仅首个）
  - T2 水平定位：justifyContent/alignItems/padding 偏移不再丢失
  - T3 CJK 宽字符：合并/间隙填充按显示宽度推进（修复前 ``中 文`` 错位）
  - T4 边框与 Line 行共存：Line 快路径后边框/后续兄弟仍正确合并
  - T5 None/True/False 子级：渲染为空（修复前 None → "None" 文本行）
  - T6 FrameBuilder.append_line：不额外插入空行
  - T7 row 显式宽度：约束子节点可用宽度（修复前按父宽溢出）
"""

from __future__ import annotations

from src.tui.ink import h, BOX, TEXT, SPACER
from src.tui.ink.element import h as mk_h
from src.tui.ink.output import Frame, FrameBuilder, Line, StyledRun
from src.tui.ink.reconciler import Reconciler
from src.tui.ink import components as _components


def _render(el, width: int = 30) -> Frame:
    root = Reconciler.create_root()
    recon = Reconciler()
    recon.render(root, el, width, 24)
    return _components.render_frame(root, width)


def _plains(frame: Frame) -> list[str]:
    return [line.plain for line in frame.lines]


class TestRowOfTexts:
    """T1 — row 方向多个 TEXT 兄弟全部绘制。"""

    def test_three_texts_all_painted(self):
        frame = _render(h(BOX, {"flexDirection": "row"}, [
            h(TEXT, {"children": "AAA"}),
            h(TEXT, {"children": "BBB"}),
            h(TEXT, {"children": "CCC"}),
        ]))
        assert _plains(frame) == ["AAABBBCCC"]

    def test_row_with_margin_separates(self):
        frame = _render(h(BOX, {"flexDirection": "row", "margin": 1}, [
            h(TEXT, {"children": "AAA"}),
            h(TEXT, {"children": "BBB"}),
        ]))
        assert _plains(frame) == ["AAA BBB"]

    def test_row_text_plus_box(self):
        frame = _render(h(BOX, {"flexDirection": "row"}, [
            h(TEXT, {"children": "AAA"}),
            h(BOX, {"border": 1}, [h(TEXT, {"children": "X"})]),
        ]))
        assert frame.height >= 3
        assert frame.lines[0].plain.startswith("AAA")
        assert "┌" in frame.lines[0].plain


class TestHorizontalPositioning:
    """T2 — 水平定位（行首空格不再丢失）。"""

    def test_row_justify_center(self):
        frame = _render(h(BOX, {"width": 20, "flexDirection": "row", "justifyContent": "center"}, [
            h(TEXT, {"children": "AAA"}),
            h(TEXT, {"children": "BB"}),
        ]))
        assert _plains(frame)[0] == "       AAABB"

    def test_row_justify_flex_end(self):
        frame = _render(h(BOX, {"width": 20, "flexDirection": "row", "justifyContent": "flex-end"}, [
            h(TEXT, {"children": "AAA"}),
        ]))
        assert _plains(frame)[0] == "                 AAA"

    def test_row_justify_space_between(self):
        frame = _render(h(BOX, {"width": 20, "flexDirection": "row", "justifyContent": "space-between"}, [
            h(TEXT, {"children": "AAA"}),
            h(TEXT, {"children": "BB"}),
        ]))
        assert _plains(frame)[0] == "AAA               BB"

    def test_column_align_items_center(self):
        frame = _render(h(BOX, {"width": 20, "alignItems": "center"}, [
            h(TEXT, {"children": "AAA"}),
            h(TEXT, {"children": "BB"}),
        ]))
        assert _plains(frame) == ["        AAA", "         BB"]

    def test_padded_box_keeps_padding(self):
        frame = _render(h(BOX, {"padding": 1, "border": 1, "width": 15}, [
            h(TEXT, {"children": "hello"}),
        ]))
        assert _plains(frame)[2] == "│ hello       │"
        assert _plains(frame)[1] == "│             │"

    def test_nested_row_inside_border(self):
        frame = _render(h(BOX, {"padding": 1, "border": 1, "width": 20}, [
            h(BOX, {"flexDirection": "row"}, [
                h(TEXT, {"children": "AAA"}),
                h(TEXT, {"children": "BBB"}),
            ]),
        ]))
        assert _plains(frame)[2] == "│ AAABBB           │"


class TestCJKWidth:
    """T3 — CJK 宽字符（列键按显示宽度）。"""

    def test_cjk_row_center_no_fake_gap(self):
        frame = _render(h(BOX, {"width": 16, "flexDirection": "row", "justifyContent": "center"}, [
            h(TEXT, {"children": "AB"}),
            h(TEXT, {"children": "中文"}),
        ]))
        assert _plains(frame)[0] == "     AB中文"

    def test_cjk_padded_box(self):
        frame = _render(h(BOX, {"padding": 1, "border": 1, "width": 10}, [
            h(TEXT, {"children": "中文"}),
        ]))
        assert _plains(frame)[2] == "│ 中文   │"


class TestBorderWithLineRows:
    """T4 — Line 快路径与边框/兄弟共存。"""

    def test_border_after_line_row(self):
        """x==0 TEXT 先写 Line 行，随后边框/兄弟需合并而不覆盖。"""
        frame = _render(h(BOX, {"flexDirection": "row"}, [
            h(TEXT, {"children": "A"}),
            h(BOX, {"border": 1}, [h(TEXT, {"children": "B"})]),
        ]), width=30)
        row0 = frame.lines[0].plain
        assert row0.startswith("A")
        assert "┌" in row0

    def test_two_text_rows_different_y(self):
        frame = _render(h(BOX, None, [
            h(TEXT, {"children": "first"}),
            h(TEXT, {"children": "second"}),
        ]))
        assert _plains(frame) == ["first", "second"]


class TestNoneChildren:
    """T5 — None/True/False 子级渲染为空。"""

    def test_none_child_ignored(self):
        frame = _render(h(BOX, None, [
            h(TEXT, {"children": "AAA"}),
            None,
            h(TEXT, {"children": "BBB"}),
        ]))
        assert _plains(frame) == ["AAA", "BBB"]

    def test_bool_child_ignored(self):
        frame = _render(h(BOX, None, [
            h(TEXT, {"children": "AAA"}),
            False,
            True,
            h(TEXT, {"children": "BBB"}),
        ]))
        assert _plains(frame) == ["AAA", "BBB"]

    def test_none_in_nested_list_ignored(self):
        frame = _render(h(BOX, None, [
            [h(TEXT, {"children": "AAA"}), None, h(TEXT, {"children": "BBB"})],
        ]))
        assert _plains(frame) == ["AAA", "BBB"]


class TestFrameBuilderAppendLine:
    """T6 — FrameBuilder.append_line 不额外插入空行。"""

    def test_append_line_after_content(self):
        fb = FrameBuilder(width=20)
        fb.append("aaa")
        fb.newline()
        fb.append_line(Line.of("BBB"))
        fb.append("ccc")
        assert [l.plain for l in fb.build().lines] == ["aaa", "BBB", "ccc"]

    def test_append_line_first(self):
        fb = FrameBuilder(width=20)
        fb.append_line(Line.of("BBB"))
        fb.append("ccc")
        assert [l.plain for l in fb.build().lines] == ["BBB", "ccc"]


class TestRowExplicitWidth:
    """T7 — row 显式宽度约束子节点（修复前按父可用宽度溢出）。"""

    def test_row_width_wraps_children(self):
        frame = _render(h(BOX, {"width": 8, "flexDirection": "row"}, [
            h(TEXT, {"children": "AAA"}),
            h(TEXT, {"children": "BBB"}),
            h(TEXT, {"children": "CCC"}),
        ]))
        # 8 列内换行 → 至少 2 行，且不超 8 列
        assert frame.height >= 2
        for line in frame.lines:
            assert len(line.plain) <= 8

    def test_row_width_justify_center(self):
        frame = _render(h(BOX, {"width": 8, "flexDirection": "row", "justifyContent": "center"}, [
            h(TEXT, {"children": "AAA"}),
            h(TEXT, {"children": "BBB"}),
        ]))
        assert _plains(frame)[0] == " AAABBB"
