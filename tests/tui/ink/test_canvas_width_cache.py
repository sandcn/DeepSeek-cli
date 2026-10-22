"""画布零宽/宽度模型 + StaticLines 合并行缓存 回归测试（2026-10-23）。

覆盖本轮修复与优化：

1. 宽度模型（``src/_text_width.cluster_width``）：孤立/尾随 ZWJ 不额外计宽
   （``"1\\u200d"`` 宽 1），emoji ZWJ 组合 / 肤色修饰符语义不变——修复前
   ``"1\\u200d"`` 计 2 列，画布列键往返丢字符、行宽虚高；
2. 画布行转换/合并：行首零宽字符位于独立 run 时不再被单宽快路径覆盖
   （``_line_as_dict`` / ``_merge_line_dict``），且 ``_merge_line`` 快路径与
   dict 路径逐字等价；
3. 行级比较器：``_lines_equal`` 与 ``Line.__eq__`` 值语义一致；
   ``_is_tail_shifted`` 越界参数返回 False（修复前空范围误判为 True）；
4. StaticLines ``box.x != 0``（缩进/带边框容器）跨帧合并行缓存：未变化行
   复用同一 Line（行级 diff 身份短路）、内容/base 行/resize/lines 替换时
   正确失效，且输出与「每帧无缓存重算」逐行等价。
"""

from __future__ import annotations

import itertools

from src._text_width import cluster_width
from src.tui._width import wcswidth_simple
from src.tui.core.style import Style
from src.tui.ink import Box, Text, components, h
from src.tui.ink import _frame_diff as fd
from src.tui.ink._paint_canvas import (
    _canvas_row_to_line,
    _line_as_dict,
    _merge_line,
    _merge_line_dict,
)
from src.tui.ink.output import Frame, Line
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.widgets.staticlines import StaticLines


def _line(*parts) -> Line:
    line = Line()
    for text, style in parts:
        line.append(text, style)
    return line


def _snap(row) -> tuple:
    line = _canvas_row_to_line(row)
    return (line.plain, line.width)


def _clone_row(row):
    if isinstance(row, Line):
        return row.clone()
    if isinstance(row, dict):
        return dict(row)
    return None


def _both(row, x: int, add: Line) -> tuple:
    return (
        _snap(_merge_line(_clone_row(row), x, add)),
        _snap(_merge_line_dict(_clone_row(row), x, add)),
    )


# ── 1. 宽度模型：孤立/尾随 ZWJ ───────────────────────────────


def test_stray_zwj_does_not_add_width() -> None:
    assert wcswidth_simple("1\u200d") == 1
    assert cluster_width("1\u200d") == 1
    assert cluster_width("\u200d") == 0
    assert Line.of("1\u200d").width == 1
    # emoji ZWJ 组合 / 肤色修饰符 / 组合标记语义不变
    assert cluster_width("👨\u200d👩\u200d👦") == 2
    assert wcswidth_simple("👨\u200d👩\u200d👦") == 2
    assert cluster_width("👍🏽") == 2
    assert cluster_width("e\u0301") == 1
    assert cluster_width("中") == 2


# ── 2. 画布行往返 / 合并 ─────────────────────────────────────


def test_canvas_roundtrip_preserves_trailing_zwj_run() -> None:
    # 修复前：dict 里键 3 为 "1\u200d"（列宽被算成 2），键 4 的 "X" 被跳过丢失。
    line = _line(("Y2c1", Style(fg=1)), ("\u200dX1\u200d1──", None))
    back = _canvas_row_to_line(_line_as_dict(line))
    assert back.plain == line.plain
    assert back.width == line.width


def test_canvas_roundtrip_preserves_leading_zero_width_run() -> None:
    # 修复前：行首零宽字符在独立 run 时被后续单宽快路径覆盖（"bb" 覆盖键 0）。
    line = _line(("\u200b", Style(fg=2)), ("bb", None))
    assert _snap(_line_as_dict(line)) == ("\u200bbb", 2)
    back = _canvas_row_to_line(_line_as_dict(line))
    assert back.plain == line.plain
    assert back.width == line.width


def test_merge_line_zero_width_cases_match_dict_path() -> None:
    cases = [
        (_line(("Y2c1", Style(fg=1)), ("\u200dX1\u200d1──", None)), 0, _line(("ab", None))),
        (_line(("\u200b", Style(fg=2)), ("bb", None)), 0, _line(("X", None))),
        (_line(("abc", None)), 1, _line(("\u200b", Style(fg=3)))),
        (_line(("abc", None)), 3, _line(("\u200bmid", None))),
        ({0: ("\u200b", None, None)}, 0, _line(("abc", None))),
    ]
    for row, x, add in cases:
        fast, slow = _both(row, x, add)
        assert fast == slow, (row.plain if isinstance(row, Line) else row, x, add.plain)


def test_canvas_roundtrip_deterministic_matrix() -> None:
    pieces = ["ab", "\u200b", "\u200dX", "中", "🙂", "e\u0301", "─", "\ufe0f"]
    for first, second in itertools.product(pieces, repeat=2):
        for style in (None, Style(fg=4)):
            line = _line((first, style), (second, None))
            back = _canvas_row_to_line(_line_as_dict(line))
            assert back.plain == line.plain, (first, second)
            assert back.width == line.width, (first, second)


# ── 3. 行级比较器 ────────────────────────────────────────────


def test_lines_equal_matches_line_eq_semantics() -> None:
    pairs = [
        (_line(("abc", None)), _line(("abc", None))),
        (_line(("abc", None)), _line(("abd", None))),
        (_line(("ab", None)), _line(("a", None), ("b", None))),
        (_line(("abc", Style(fg=1))), _line(("abc", Style(fg=2)))),
        (_line(("中", None)), _line(("ab", None))),
        (Line(), Line()),
    ]
    # 双方宽度缓存均已填充（diff 热路径常态）与均未填充两种状态
    for a, b in list(pairs):
        assert fd._lines_equal(a, b) == (a == b)
    for a, b in pairs:
        a.width
        b.width
        assert fd._lines_equal(a, b) == (a == b)


def test_is_tail_shifted_rejects_out_of_range() -> None:
    a, b = _line(("a", None)), _line(("b", None))
    prev = Frame([a, b])
    new = Frame([_line(("x", None)), a, b])
    assert fd._is_tail_shifted(prev, new, 0, 1) is True
    assert fd._is_tail_shifted(prev, new, 3, 1) is False  # i > prev.height
    assert fd._is_tail_shifted(prev, new, -1, 1) is False


# ── 4. StaticLines 合并行缓存（box.x != 0） ──────────────────


def _tree(lines, live: str = "status", border_color=None):
    props = {"border": 1, "width": 60}
    if border_color is not None:
        props["borderColor"] = border_color
    return h(
        Box,
        props,
        h(StaticLines, {"lines": lines}),
        h(Text, None, live),
    )


def _render(reconciler, root, element, width: int = 60) -> Frame:
    reconciler.render(root, element, width, 24)
    return components.render_frame(root, width)


def _plain(frame: Frame) -> list:
    return [line.plain for line in frame.lines]


def test_indented_static_rows_reuse_merged_lines() -> None:
    lines = [_line((f"row {i}", None)) for i in range(12)]
    reconciler = Reconciler(None)
    root = Reconciler.create_root()
    first = _render(reconciler, root, _tree(lines))
    second = _render(reconciler, root, _tree(lines))

    assert _plain(first) == _plain(second)
    assert all(isinstance(line, Line) for line in second.lines)
    row = second.lines[1].plain
    assert row.startswith("│") and row.endswith("│") and "row 0" in row
    # 合并行缓存命中：未变化行跨帧同一 Line 对象（diff 身份短路）
    same = sum(a is b for a, b in zip(first.lines, second.lines))
    assert same >= len(first.lines) - 2


def test_static_rows_cache_invalidates_on_content_change() -> None:
    lines = [_line((f"row {i}", None)) for i in range(6)]
    reconciler = Reconciler(None)
    root = Reconciler.create_root()
    first = _render(reconciler, root, _tree(lines))

    lines[2] = _line(("CHANGED", None))
    second = _render(reconciler, root, _tree(lines))
    assert "CHANGED" in second.lines[3].plain
    assert first.lines[1] is second.lines[1]  # 其他内容行仍命中缓存


def test_static_rows_cache_handles_inplace_growth() -> None:
    lines = [_line((f"row {i}", None)) for i in range(4)]
    reconciler = Reconciler(None)
    root = Reconciler.create_root()
    first = _render(reconciler, root, _tree(lines))

    lines.append(_line(("appended", None)))
    second = _render(reconciler, root, _tree(lines))
    assert any("appended" in line.plain for line in second.lines)
    assert first.lines[1] is second.lines[1]


def test_static_rows_cache_width_sensitive() -> None:
    lines = [_line(("x" * 40, None)) for _ in range(4)]
    reconciler = Reconciler(None)
    root = Reconciler.create_root()
    _render(reconciler, root, _tree(lines), width=60)
    narrow = _render(reconciler, root, _tree(lines), width=30)
    assert all(line.width <= 30 for line in narrow.lines)
    assert any("x" * 20 in line.plain for line in narrow.lines)


def test_static_rows_cache_invalidates_on_base_style_change() -> None:
    lines = [_line((f"row {i}", None)) for i in range(4)]
    reconciler = Reconciler(None)
    root = Reconciler.create_root()
    first = _render(reconciler, root, _tree(lines, border_color=23))
    second = _render(reconciler, root, _tree(lines, border_color=2))
    # 边框 base 行内容变化 → 合并行必须重建（不得复用旧样式）
    assert first.lines[1] is not second.lines[1]
    assert second.lines[1].runs[0].style.fg == 2


def test_static_rows_cached_output_matches_uncached_reference() -> None:
    lines = [_line((f"row {i}", Style(fg=i % 3)), (f" tail {i}", None)) for i in range(8)]
    reconciler = Reconciler(None)
    root = Reconciler.create_root()
    cached = _render(reconciler, root, _tree(lines, border_color=4))

    fiber = components._find_committed_chat(root)
    assert fiber is not None
    assert fiber._merged_rows_cache is not None

    # 每帧清空缓存 → 逐行重算（参考实现）
    fiber._merged_rows_cache = None
    reference = _render(reconciler, root, _tree(lines, border_color=4))

    assert _plain(cached) == _plain(reference)
    assert [line.width for line in cached.lines] == [
        line.width for line in reference.lines
    ]
    assert [[(run.text, run.style) for run in line.runs] for line in cached.lines] == [
        [(run.text, run.style) for run in line.runs] for line in reference.lines
    ]


def test_static_rows_cache_uncacheable_base_still_correct() -> None:
    # 边框 + backgroundColor 铺满整行（base 键数超阈值 → 不缓存），逐帧合并
    # 仍需正确（内容与底色都不丢）。
    lines = [_line((f"row {i}", None)) for i in range(4)]
    reconciler = Reconciler(None)
    root = Reconciler.create_root()

    def tree(bg):
        return h(
            Box,
            {"width": 40, "border": 1, "backgroundColor": bg},
            h(StaticLines, {"lines": lines}),
        )

    first = _render(reconciler, root, tree(1), width=40)
    second = _render(reconciler, root, tree(2), width=40)
    assert _plain(first) == _plain(second)
    assert second.lines[1].plain.startswith("│") and "row 0" in second.lines[1].plain
    # 背景色变化必须反映到合并行（不得复用旧底色）
    assert any(
        run.style is not None and run.style.bg == 2
        for run in second.lines[1].runs
    )


def test_static_rows_background_preserved_at_x_zero() -> None:
    # 修复前：x==0 的前缀复用直接用缓存行替换画布 → 父容器底色丢失。
    lines = [_line((f"row {i}", None)) for i in range(4)]
    reconciler = Reconciler(None)
    root = Reconciler.create_root()

    def tree(bg):
        return h(
            Box,
            {"width": 40, "backgroundColor": bg},
            h(StaticLines, {"lines": lines}),
        )

    first = _render(reconciler, root, tree(1), width=40)
    second = _render(reconciler, root, tree(2), width=40)
    assert first.lines[0].plain.startswith("row 0")
    assert "row 3" in second.lines[3].plain
    # 底色保留且随 props 变化（冲突持续期间前缀缓存关闭）
    assert any(
        run.style is not None and run.style.bg == 2
        for run in second.lines[0].runs
    )
    fiber = components._find_committed_chat(root)
    assert fiber is not None and fiber._committed_prefix is None


def test_static_rows_cache_cleared_for_x_zero_path() -> None:
    lines = [_line((f"row {i}", None)) for i in range(4)]
    reconciler = Reconciler(None)
    root = Reconciler.create_root()
    _render(reconciler, root, _tree(lines))  # x != 0：建立缓存
    fiber = components._find_committed_chat(root)
    assert fiber is not None and fiber._merged_rows_cache is not None

    # 顶层（x == 0）路径：改用前缀缓存，合并行缓存被清空
    element = h(StaticLines, {"lines": lines})
    _render(reconciler, root, element)
    fiber = components._find_committed_chat(root)
    assert fiber is not None
    assert fiber._merged_rows_cache is None
