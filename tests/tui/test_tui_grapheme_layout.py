"""字形簇（grapheme cluster）布局回归测试。

覆盖 TUI 文本布局的宽度/换行/截断/绘制链路：ZWJ 组合 emoji、肤色修饰符、
keycap、国旗（Regional Indicator）必须作为**原子字形簇**处理——
  - 换行/截断不把组合 emoji 拦腰拆开（悬空 ZWJ / 孤立肤色修饰符）；
  - 行宽按终端实际列宽计算（逐码点求和会高估 skin tone / 低估 keycap）；
  - run 边界落在同一字形簇内时 ``Line.width`` / 截断/填充仍按拼接文本测宽。
"""

from __future__ import annotations

from src._text_width import grapheme_clusters, string_width
from src.tui._input_layout import (
    _compute_cursor_visual_pos,
    _expand_tabs,
    _tab_pos_to_expanded,
    _wrap_by_width,
)
from src.tui._width import truncate_width, wcswidth_simple
from src.tui.app._popup_builder import _highlight_line
from src.tui.app._welcome import display_width, truncate_segments
from src.tui.app.toolcard import _apply_line_bg
from src.tui.app.trace_view import _truncate_text
from src.tui.core.style import Style
from src.tui.ink import Line, StyledRun, h
from src.tui.ink._layout_measure import _runs_natural_width
from src.tui.ink._paint_canvas import _line_as_dict, _slice_run_text
from src.tui.ink._runs_utils import (
    _runs_total_width,
    truncate_line,
    truncate_runs,
    truncate_runs_ellipsis,
    truncate_runs_middle,
    truncate_runs_start,
    wrap_runs_by_width,
)
from src.tui.ink.output import FrameBuilder
from src.tui.ink.widgets import Divider
from src.tui.ink.widgets._display_common import _truncate_to_width
from src.tui.ink.widgets.gradient import _gradient_runs
from tests.tui.ink._harness import Harness

_ZWJ_FAMILY = "👨\u200d👩\u200d👧\u200d👦"
_SKIN = "👍🏽"
_KEYCAP = "1️⃣"
_FLAG = "🇨🇳"
_RAINBOW_FLAG = "🏳️\u200d🌈"


def _assert_whole_clusters(text: str) -> None:
    """输出文本不得包含悬空 ZWJ / 孤立肤色修饰符 / 孤立 keycap 组合符。"""
    for cluster in grapheme_clusters(text):
        assert not cluster.endswith("\u200d"), repr(text)
        first = ord(cluster[0])
        assert first not in (0x200D, 0x20E3), repr(text)
        assert not (0x1F3FB <= first <= 0x1F3FF), repr(text)


# ── 共享宽度语义 ──────────────────────────────────────────

def test_shared_grapheme_widths():
    assert string_width(_ZWJ_FAMILY) == 2
    assert string_width(_SKIN) == 2
    assert string_width(_KEYCAP) == 2
    assert string_width(_FLAG) == 2
    assert string_width(_RAINBOW_FLAG) == 2
    assert wcswidth_simple(_ZWJ_FAMILY) == 2


# ── 输入区换行 / 制表符 / 光标 ─────────────────────────────

def test_wrap_by_width_keeps_grapheme_clusters_atomic():
    for text in (_ZWJ_FAMILY, _SKIN, _KEYCAP, _FLAG, _RAINBOW_FLAG):
        assert _wrap_by_width(text, 2) == [text]
        for segment in _wrap_by_width(text, 2):
            assert wcswidth_simple(segment) <= 2
    assert _wrap_by_width("a" + _SKIN + "b", 2) == ["a", _SKIN, "b"]


def test_wrap_by_width_never_overflows_on_narrow_width():
    for text in (_ZWJ_FAMILY, _SKIN, _KEYCAP, _FLAG):
        for width in (1, 2, 3):
            for segment in _wrap_by_width(text, width):
                assert wcswidth_simple(segment) <= width


def test_expand_tabs_uses_cluster_width_for_tab_stop():
    # 肤色 emoji 实际宽 2 → 下一 tab stop（4）补 2 空格
    assert _expand_tabs(_SKIN + "\tb", tab_width=4) == _SKIN + "  b"
    # 字符索引 3（b 之前）展开后位于第 4 列
    assert _tab_pos_to_expanded(_SKIN + "\tb", 3, tab_width=4) == 4


def test_cursor_visual_position_with_cluster_wrap():
    for pos in range(len("a" + _SKIN + "b") + 1):
        row, col = _compute_cursor_visual_pos("a" + _SKIN + "b", pos, 2)
        assert row >= 0 and 0 <= col <= 2


# ── 纯文本截断 ────────────────────────────────────────────

def test_truncate_width_keeps_clusters_atomic():
    assert truncate_width(_ZWJ_FAMILY, 2) == _ZWJ_FAMILY
    assert truncate_width(_SKIN, 2) == _SKIN
    assert truncate_width(_KEYCAP, 2) == _KEYCAP
    assert truncate_width(_FLAG, 2) == _FLAG
    for text in (_ZWJ_FAMILY, _SKIN, _KEYCAP, _FLAG):
        assert truncate_width(text, 1) == ""
    assert truncate_width("a" + _SKIN + "b", 2) == "a"
    for width in range(0, 4):
        out = truncate_width("a" + _SKIN + "b", width)
        assert wcswidth_simple(out) <= width
        _assert_whole_clusters(out)


# ── StyledRun 换行 / 截断（含 run 边界拆开字形簇） ─────────

def test_wrap_runs_handles_cluster_split_across_runs():
    runs = [StyledRun("👍"), StyledRun("🏽")]
    assert Line(runs).width == 2
    out = wrap_runs_by_width(runs, 2)
    assert len(out) == 1
    assert out[0].width == 2
    assert "".join(r.text for r in out[0].runs) == _SKIN

    lines = wrap_runs_by_width([StyledRun("1\uFE0F"), StyledRun("\u20E3")], 1)
    assert all(line.width <= 1 for line in lines)


def test_line_width_recomputes_when_cluster_split_across_runs():
    assert Line([StyledRun("👍"), StyledRun("🏽")]).width == 2
    line = Line()
    line.append("👍", Style(fg=1))
    line.append("🏽", Style(fg=2))
    assert line.width == 2
    line2 = Line()
    line2.append("1\uFE0F")
    line2.append("\u20E3")
    assert line2.width == 2


def test_truncate_family_respects_width_with_split_clusters():
    text = _KEYCAP + _FLAG
    for split in range(len(text) + 1):
        runs = [StyledRun(text[:split]), StyledRun(text[split:])]
        for width in (1, 2, 3, 4):
            for func in (
                truncate_runs,
                truncate_runs_ellipsis,
                truncate_runs_start,
                truncate_runs_middle,
            ):
                out = func(runs, width)
                plain = "".join(r.text for r in out).replace("…", "")
                assert string_width(plain) <= width, (func.__name__, split, width, out)
                _assert_whole_clusters(plain)
            line = truncate_line(Line(runs), width)
            assert line.width <= width
            _assert_whole_clusters(line.plain)


# ── FrameBuilder ──────────────────────────────────────────

def test_frame_builder_wraps_clusters_atomically():
    builder = FrameBuilder(2)
    builder.append("a" + _SKIN + "b")
    lines = builder.build().lines
    assert [line.plain for line in lines] == ["a", _SKIN, "b"]
    assert all(line.width <= 2 for line in lines)
    _assert_whole_clusters("".join(line.plain for line in lines))


def test_frame_builder_skips_oversized_cluster():
    builder = FrameBuilder(1)
    builder.append("中")
    assert builder.build().lines == []


# ── 控件截断 / 渐变 / 画布 ────────────────────────────────

def test_display_common_truncate_keeps_clusters_atomic():
    assert _truncate_to_width(_SKIN, 2) == _SKIN
    assert _truncate_to_width(_SKIN, 1) == "\u2026"
    out = _truncate_to_width(_KEYCAP + _FLAG, 3)
    assert wcswidth_simple(out) <= 3
    _assert_whole_clusters(out.replace("\u2026", ""))


def test_gradient_runs_use_grapheme_clusters():
    runs = _gradient_runs(_ZWJ_FAMILY, [45, 213])
    assert len(runs) == 1
    assert runs[0].text == _ZWJ_FAMILY
    assert runs[0].width == 2
    runs2 = _gradient_runs(_SKIN, [45, 213])
    assert len(runs2) == 1 and runs2[0].width == 2


def test_paint_canvas_keeps_cluster_in_one_cell():
    cells = _line_as_dict(Line([StyledRun("a" + _SKIN + "b")]))
    assert cells[0][0] == "a"
    assert cells[1][0] == _SKIN          # 整簇写入首列（不再残留孤立肤色格）
    assert cells[3][0] == "b"
    assert 2 not in cells
    assert _slice_run_text("a" + _SKIN + "b", 0, 2) == "a" + _SKIN


# ── 布局测量 / 视图辅助 ───────────────────────────────────

def test_runs_natural_width_with_split_cluster():
    assert _runs_natural_width([StyledRun("👍"), StyledRun("🏽")]) == 2
    assert _runs_natural_width([StyledRun("1\uFE0F"), StyledRun("\u20E3")]) == 2


def test_welcome_display_width_and_truncate_segments():
    assert display_width(_ZWJ_FAMILY) == 2
    assert display_width(_KEYCAP) == 2
    assert truncate_segments([(_SKIN, None)], 2) == [(_SKIN, None)]
    assert truncate_segments([("a" + _SKIN + "b", None)], 2) == [("a", None)]


def test_trace_view_truncate_text_keeps_clusters_atomic():
    assert _truncate_text(_KEYCAP, 2) == _KEYCAP
    assert _truncate_text(_KEYCAP, 1) == "\u2026"
    assert _truncate_text("a" + _SKIN + "b", 2) == "a\u2026"


def test_popup_highlight_uses_line_width_for_split_clusters():
    line = Line([StyledRun("👍"), StyledRun("🏽")])
    out = _highlight_line(line, 2, 237)
    assert out.width == 2
    out2 = _highlight_line(line, 4, 237)
    assert out2.width == 4
    _assert_whole_clusters("".join(r.text for r in out2.runs).rstrip(" "))


def test_toolcard_line_bg_uses_cluster_width():
    runs = [StyledRun("👍"), StyledRun("🏽")]
    out = _apply_line_bg(runs, 4, Style(bg=237))
    assert string_width("".join(r.text for r in out)) == 4
    narrow = _apply_line_bg(runs, 1, Style(bg=237))
    assert string_width("".join(r.text for r in narrow)) <= 1


def test_divider_trailing_split_cluster_line_width():
    frame = Harness(5).frame(
        h(Divider, {"width": 5, "trailing": [StyledRun("👍"), StyledRun("🏽")]})
    )
    assert frame.lines
    assert all(line.width <= 5 for line in frame.lines)


# ── 共享宽度/制表符真源（ANSI + 字形簇） ──────────────────
# 回归：``string_width`` 含 ANSI 时整串回退逐字符路径 → 组合 emoji 宽度
# 高估；``expand_tabs`` 逐码点推进列宽 → 组合 emoji 后制表位错误。

def test_shared_string_width_with_ansi_and_clusters():
    assert string_width("\x1b[31m" + _ZWJ_FAMILY + "\x1b[0m") == 2
    assert string_width("\x1b[1m" + _SKIN + "\x1b[0m") == 2
    assert string_width("a\x1b[31m" + _KEYCAP + "\x1b[0mb") == 4
    # ANSI 序列本身零宽（不与后随/前导文本拼接错算）
    assert string_width("a\x1b[0m" + _FLAG) == 3


def test_shared_expand_tabs_uses_cluster_width():
    from src._text_width import expand_tabs as shared_expand_tabs

    # ZWJ 家庭 emoji 实际宽 2 → tab stop 8 补 6 空格
    assert shared_expand_tabs(_ZWJ_FAMILY + "\tb", tab_width=8) == _ZWJ_FAMILY + " " * 6 + "b"
    assert shared_expand_tabs(_SKIN + "\tb", tab_width=8) == _SKIN + " " * 6 + "b"
    # ANSI 序列不占列
    assert shared_expand_tabs("\x1b[31mab\x1b[0m\tb", tab_width=8) == "\x1b[31mab\x1b[0m" + " " * 6 + "b"
    # ZWJ 不得吞并后续制表符（字面 \t 漏展开）
    out = shared_expand_tabs("\u200d\u200d\tb", tab_width=8)
    assert "\t" not in out
    assert out.endswith("b")
    assert out.count(" ") >= 1


def test_styled_run_expands_tab_after_zwj():
    run = StyledRun("\u200d\u200d\tb")
    assert "\t" not in run.text
    assert run.width == string_width(run.text)


# ── 字形簇断行回归（断点落在簇内部） ──────────────────────

def test_wrap_runs_word_break_inside_cluster_no_crash_or_overflow():
    # 空格被 ZWJ 吞入组合簇：按空格断行会让下一行从簇内部开始——旧实现
    # 按整簇长度推进越过行尾（IndexError）或产出超宽行。
    for text in (
        "b文🏽 \u200da\u0301\u0301文\u20e3",
        "🇨🏽👍中 \u20e3\u200dbxab 🇳",
        " \u200d\u200d\tb",
    ):
        for width in (1, 2, 3, 4, 6):
            lines = wrap_runs_by_width([StyledRun(text)], width)
            for line in lines:
                assert line.width <= width, (text, width, line.plain)


def test_wrap_runs_newline_before_extender_keeps_line_width():
    # 换行后紧跟肤色修饰符：旧实现把 "\n🏽" 视为同一簇，断行后从簇内部
    # 开始推进（IndexError / 超宽）。
    for text in ("\n🏽", "a\n🏽b", "a\n\u200d\u200d\tb"):
        for width in (1, 2, 3):
            lines = wrap_runs_by_width([StyledRun(text)], width)
            for line in lines:
                assert line.width <= width, (text, width, line.plain)


def test_truncate_start_keycap_after_ri_not_overflow():
    # RI 后的 keycap 组合符曾被当作独立零宽簇：truncate-start 选中它后与
    # 省略号拼接，宽度被误计为 2（结果超宽）。
    text = "🇳🇳🇨\u20e3x\u20e3   "
    for width in range(1, 8):
        out = truncate_runs_start([StyledRun(text)], width)
        assert _runs_total_width(out) <= width, (width, "".join(r.text for r in out))

