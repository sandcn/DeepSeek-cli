"""ANSI 辅助工具测试 — 覆盖 src/renderer/ansi/helpers.py。

验证 Run/AnsiLine 输出模型、宽度测量、ANSI 剥离、换行/截断/填充、
以及 ANSI→Run 的 SGR 解析。
"""

import pytest

from src.renderer.ansi.helpers import (
    AnsiLine,
    Run,
    ansi_to_line,
    ansi_to_runs,
    pad_line,
    parse_sgr_params,
    strip_ansi,
    truncate_line,
    visual_width,
    wrap_line,
)
from src.renderer.ansi.style import Style


# ── Run ───────────────────────────────────────────────────

def test_run_render_plain():
    assert Run("hello").render() == "hello"


def test_run_render_with_style():
    assert Run("x", Style(bold=True)).render() == "\033[1mx\033[0m"


def test_run_width_cjk():
    assert Run("ab").width == 2
    assert Run("你").width == 2  # CJK 宽字符


# ── AnsiLine ──────────────────────────────────────────────

def test_ansiline_of():
    line = AnsiLine.of("hi")
    assert line.plain == "hi"


def test_ansiline_append_merges_same_style():
    line = AnsiLine()
    line.append("a")
    line.append("b")
    assert len(line.runs) == 1
    assert line.plain == "ab"


def test_ansiline_append_different_style_separates():
    line = AnsiLine()
    line.append("a", Style(bold=True))
    line.append("b")
    assert len(line.runs) == 2


def test_ansiline_render():
    line = AnsiLine.of("hi", Style(bold=True))
    assert line.render() == "\033[1mhi\033[0m"


def test_ansiline_width():
    line = AnsiLine()
    line.append("ab")
    line.append("你")
    assert line.width == 4


def test_ansiline_clone_independent():
    line = AnsiLine.of("a")
    clone = line.clone()
    clone.append("b")
    assert line.plain == "a"
    assert clone.plain == "ab"


# ── visual_width / strip_ansi ─────────────────────────────

def test_visual_width_plain():
    assert visual_width("hello") == 5


def test_visual_width_cjk():
    assert visual_width("你好") == 4


def test_visual_width_strips_ansi():
    assert visual_width("\x1b[31mred\x1b[0m") == 3


def test_strip_ansi_removes_sequences():
    assert strip_ansi("\x1b[31mred\x1b[0m") == "red"


def test_strip_ansi_no_ansi():
    assert strip_ansi("plain") == "plain"


# ── wrap_line ─────────────────────────────────────────────

def test_wrap_line_word_boundary():
    lines = wrap_line(AnsiLine.of("hello world"), 5)
    assert [l.plain for l in lines] == ["hello", "world"]


def test_wrap_line_no_wrap_when_fits():
    lines = wrap_line(AnsiLine.of("hi"), 5)
    assert [l.plain for l in lines] == ["hi"]


def test_wrap_line_hard_split_long_word():
    lines = wrap_line(AnsiLine.of("abcdef"), 3)
    assert [l.plain for l in lines] == ["abc", "def"]


def test_wrap_line_empty():
    assert wrap_line(AnsiLine(), 5) == []


# ── truncate_line ─────────────────────────────────────────

def test_truncate_line_within_limit():
    line = truncate_line(AnsiLine.of("hello"), 10)
    assert line.plain == "hello"


def test_truncate_line_shortens():
    line = truncate_line(AnsiLine.of("hello"), 3)
    assert line.plain == "hel"


def test_truncate_line_negative():
    assert truncate_line(AnsiLine.of("hello"), -1).plain == ""


# ── pad_line ──────────────────────────────────────────────

def test_pad_line_adds_spaces():
    line = pad_line(AnsiLine.of("hi"), 5)
    assert line.plain == "hi   "


def test_pad_line_truncates_overflow():
    line = pad_line(AnsiLine.of("hello"), 3)
    assert line.plain == "hel"


# ── parse_sgr_params ──────────────────────────────────────

def test_parse_sgr_empty_reset():
    style, reset = parse_sgr_params("")
    assert reset is True
    assert style is None or not style


def test_parse_sgr_bold():
    style, reset = parse_sgr_params("1")
    assert reset is False
    assert style.bold is True


def test_parse_sgr_fg_31():
    style, reset = parse_sgr_params("31")
    assert style.fg == 31


def test_parse_sgr_256_color():
    style, _ = parse_sgr_params("38;5;42")
    assert style.fg == 42


def test_parse_sgr_rgb_color():
    style, _ = parse_sgr_params("38;2;1;2;3")
    assert style.fg == (1, 2, 3)


def test_parse_sgr_reset_zero():
    _, reset = parse_sgr_params("0")
    assert reset is True


# ── ansi_to_runs / ansi_to_line ───────────────────────────

def test_ansi_to_runs_bold():
    runs = ansi_to_runs("\x1b[1mbold\x1b[0m")
    assert len(runs) == 1
    assert runs[0].text == "bold"
    assert runs[0].style.bold is True


def test_ansi_to_runs_plain():
    runs = ansi_to_runs("plain")
    assert len(runs) == 1
    assert runs[0].text == "plain"


def test_ansi_to_line():
    line = ansi_to_line("\x1b[31mred\x1b[0m")
    assert line.plain == "red"
