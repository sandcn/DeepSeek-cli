"""画布与行级比较器优化回归测试（2026-10-23）。

覆盖本次性能优化引入的关键契约：
  - ``_merge_line`` 的 run 级快路径与 dict 路径逐字等价（ASCII 全覆盖 /
    末尾拼接 / 前缀空格 / 空行），非 ASCII 行回退后行为不变；
  - ``_canvas_row_to_line`` 的间距 / 宽字符 / 零宽字符 / 超链接 / 样式；
  - ``render_frame`` 跨帧 Line 复用：未变化帧行对象稳定、内容变化与
    resize 时正确失效；
  - ``first_diff_line`` / ``_diff_runs`` / ``_is_tail_shifted`` /
    ``_find_tail_anchor`` 的返回契约与快速拒绝语义；
  - ``Line.__eq__`` 的身份/长度/宽度快速拒绝不改变值语义。
"""

from __future__ import annotations

from src.tui.core.style import Style
from src.tui.ink import Box, Row, Text, h
from src.tui.ink import _frame_diff as fd
from src.tui.ink._paint_canvas import (
    _canvas_row_to_line,
    _line_as_dict,
    _merge_line,
    _merge_line_dict,
)
from src.tui.ink.components import render_frame
from src.tui.ink.diff import first_diff_line
from src.tui.ink.output import Frame, Line
from src.tui.ink.reconciler import Reconciler


def _line(text: str, style: Style | None = None, link: str | None = None) -> Line:
    line = Line()
    line.append(text, style, link)
    return line


def _snap(row) -> tuple:
    line = _canvas_row_to_line(row)
    return (
        line.plain,
        line.width,
        [(run.text, run.style, run.link) for run in line.runs],
    )


def _both(row, x: int, add: Line) -> tuple:
    """分别走快路径与 dict 路径，返回 ``(fast_snapshot, slow_snapshot)``。"""
    base_row = _line_as_dict(row) if isinstance(row, Line) else row
    return (
        _snap(_merge_line(row, x, add)),
        _snap(_merge_line_dict(base_row, x, add)),
    )


# ── 画布合并：快路径与 dict 路径等价 ──────────────────────────


def test_merge_line_ascii_full_cover_matches_dict_path() -> None:
    row = _line("abcdef")
    add = _line("XYZ0123456789")
    fast, slow = _both(row, 0, add)
    assert fast == slow
    assert fast[0] == "XYZ0123456789"


def test_merge_line_ascii_tail_concat_and_gap_match_dict_path() -> None:
    row = _line("left")
    for x in (4, 5, 12):
        fast, slow = _both(row, x, _line("mid", Style(fg=2), "https://x"))
        assert fast == slow
        assert fast[0] == "left" + " " * (x - 4) + "mid"


def test_merge_line_ascii_empty_row_matches_dict_path() -> None:
    add = _line("abc", Style(fg=3))
    for x in (0, 1, 5):
        fast, slow = _both(None, x, add)
        assert fast == slow
        assert fast[0] == " " * x + "abc"


def test_merge_line_non_ascii_falls_back_and_matches_dict_path() -> None:
    # CJK / emoji / 组合标记 / 零宽连接符：必须走 dict 路径，结果逐字一致。
    cases = [
        (_line("中文abc"), 0, _line("🙂x中")),
        (_line("a中b"), 2, _line("文🙂")),
        (_line("e\u0301x"), 0, _line("y")),
        (_line("abc"), 1, _line("\u200d中")),
    ]
    for row, x, add in cases:
        fast, slow = _both(row, x, add)
        assert fast == slow, (row.plain, x, add.plain)


def test_merge_line_single_width_border_uses_fast_path() -> None:
    # 边框字符（width-1 非 ASCII）应走 run 级快路径，且与 dict 路径等价。
    box = _line("┌" + "─" * 8 + "┐")
    merged = _merge_line(None, 0, box)
    assert isinstance(merged, Line)
    assert merged.plain == box.plain
    fast, slow = _both(None, 0, box)
    assert fast == slow
    assert _snap(_line_as_dict(box))[0] == box.plain


def test_merge_line_empty_line_keeps_target_row() -> None:
    row = _line("abc")
    assert _merge_line(row, 3, Line()) is row
    assert _merge_line(None, 0, Line()) == {}


# ── 画布行转换 ────────────────────────────────────────────────


def test_canvas_row_to_line_gaps_wide_and_zero_width() -> None:
    row = {
        0: ("a", None, None),
        2: ("中", None, None),
        4: ("b", Style(fg=1), "https://x"),
    }
    line = _canvas_row_to_line(row)
    assert line.plain == "a 中b"
    assert line.width == 5
    assert line.runs[-1].link == "https://x"

    zrow = {0: ("e\u0301", None, None), 1: ("x", None, None)}
    zline = _canvas_row_to_line(zrow)
    assert zline.plain == "e\u0301x"
    assert zline.width == 2


def test_canvas_row_to_line_skips_overlapped_wide_second_col() -> None:
    # 键 1 落在宽字符「中」的第二列 → 必须跳过该键（不得死循环、不得重复）
    row = {0: ("中", None, None), 1: ("X", None, None), 2: ("a", None, None)}
    line = _canvas_row_to_line(row)
    assert line.plain == "中a"
    assert line.width == 3


# ── 跨帧 Line 复用 ────────────────────────────────────────────


def _tree(row_text: str = "left") -> object:
    return h(
        Box,
        {"border": 1, "width": 40},
        h(
            Row,
            {"height": 1},
            h(Text, {"width": 10}, row_text),
            h(Text, {"width": 10}, "mid"),
            h(Text, {"width": 10}, "right"),
        ),
        h(Text, None, "中文内容"),
    )


def _render(root, element, width: int = 40) -> Frame:
    Reconciler(None).render(root, element, width, 24)
    return render_frame(root, width)


def test_render_frame_reuses_lines_across_unchanged_frames() -> None:
    root = Reconciler.create_root()
    frame1 = _render(root, _tree())
    frame2 = _render(root, _tree())
    assert len(frame1.lines) == len(frame2.lines)
    # 边框顶/底行每帧新建 Line（不进缓存），其余字典行/文本行应保持同一对象。
    same = sum(a is b for a, b in zip(frame1.lines, frame2.lines))
    assert same >= len(frame1.lines) - 2
    assert [line.plain for line in frame1.lines] == [
        line.plain for line in frame2.lines
    ]


def test_render_frame_invalidates_reused_line_when_content_changes() -> None:
    root = Reconciler.create_root()
    frame1 = _render(root, _tree("left"))
    frame2 = _render(root, _tree("LEFT"))
    assert any(a is not b for a, b in zip(frame1.lines, frame2.lines))
    assert any("LEFT" in line.plain for line in frame2.lines)


def test_render_frame_cache_is_width_sensitive() -> None:
    root = Reconciler.create_root()
    wide = _render(root, _tree(), width=40)
    narrow = _render(root, _tree(), width=16)
    assert all(line.width <= 16 for line in narrow.lines)
    assert any(a is not b for a, b in zip(wide.lines, narrow.lines))


# ── 行级比较器契约 ────────────────────────────────────────────


def test_line_eq_identity_length_and_width_fast_paths() -> None:
    a = _line("abc")
    assert a == a
    assert _line("abc") == _line("abc")
    assert _line("abc") != _line("abcd")
    assert _line("中") != _line("ab")
    assert (a == object()) is False


def test_first_diff_line_identity_and_stable_prefix() -> None:
    prefix = [_line(f"p{i}") for i in range(5)]
    tail_a = [_line("tail0"), _line("tail1")]
    tail_b = [_line("tail0"), _line("TAIL1")]
    a = Frame(prefix + tail_a, stable_prefix=prefix, stable_prefix_len=5)
    b = Frame(prefix + tail_b, stable_prefix=prefix, stable_prefix_len=5)
    assert first_diff_line(a, a) == -1
    assert first_diff_line(a, b) == 6


def test_first_diff_line_height_only_difference() -> None:
    a = Frame([_line("a")])
    b = Frame([_line("a"), _line("b")])
    assert first_diff_line(a, b) == 1
    assert first_diff_line(b, a) == 1


def test_diff_runs_reports_changed_interval() -> None:
    common0 = _line("same0")
    common1 = _line("same1")
    prev = Frame([common0, _line("old"), common1])
    new = Frame([common0, _line("new"), common1])
    assert fd._diff_runs(prev, new, 3) == [(1, 2)]
    assert fd._diff_runs(prev, prev, 3) == []


def test_tail_shift_and_anchor_contracts() -> None:
    a, b = _line("a"), _line("b")
    prev = Frame([a, b])
    new = Frame([_line("x"), a, b])
    assert fd._is_tail_shifted(prev, new, 0, 1) is True
    assert fd._find_tail_anchor(Frame([a, b, _line("c")]), Frame([_line("x"), b, _line("c")]), 0) == 1
