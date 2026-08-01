"""测试 renderer/ansi/helpers.py — Run/AnsiLine 模型 + 换行/截断/ANSI→Style。"""

from __future__ import annotations

from src.renderer.ansi.style import Style
from src.renderer.ansi.helpers import (
    Run,
    AnsiLine,
    wrap_line,
    truncate_line,
    pad_line,
    strip_ansi,
    visual_width,
    parse_sgr_params,
    ansi_to_runs,
    ansi_to_line,
)


class TestRun:
    def test_render_plain(self):
        assert Run("abc").render() == "abc"

    def test_render_styled(self):
        assert Run("x", Style(fg=45)).render() == "\033[38;5;45mx\033[0m"

    def test_width_cjk(self):
        assert Run("中").width == 2


class TestAnsiLine:
    def test_of(self):
        line = AnsiLine.of("hi")
        assert line.plain == "hi"
        assert line.render() == "hi"

    def test_append_merges(self):
        line = AnsiLine()
        line.append("a")
        line.append("b")
        assert len(line.runs) == 1
        assert line.plain == "ab"

    def test_append_separate_styles(self):
        line = AnsiLine()
        line.append("a", Style(fg=45))
        line.append("b", Style(fg=46))
        assert len(line.runs) == 2

    def test_width(self):
        line = AnsiLine()
        line.append("ab")
        line.append("中")
        assert line.width == 4

    def test_clone(self):
        line = AnsiLine.of("x")
        c = line.clone()
        c.append("y")
        assert line.plain == "x"
        assert c.plain == "xy"


class TestWrap:
    def test_no_wrap(self):
        lines = wrap_line(AnsiLine.of("abc"), 10)
        assert len(lines) == 1

    def test_wrap_by_width(self):
        lines = wrap_line(AnsiLine.of("abcdefgh"), 3)
        assert [l.plain for l in lines] == ["abc", "def", "gh"]

    def test_wrap_cjk(self):
        lines = wrap_line(AnsiLine.of("中文测试"), 3)
        assert "".join(l.plain for l in lines) == "中文测试"
        assert all(l.width <= 3 for l in lines)

    def test_wrap_styles_preserved(self):
        line = AnsiLine()
        line.append("abcdef", Style(fg=45))
        lines = wrap_line(line, 3)
        assert len(lines) == 2
        assert all(all(r.style is not None for r in l.runs) for l in lines)


class TestTruncate:
    def test_truncate(self):
        assert truncate_line(AnsiLine.of("abcdef"), 3).plain == "abc"

    def test_truncate_cjk_boundary(self):
        assert truncate_line(AnsiLine.of("ab中"), 3).plain == "ab"

    def test_within_width(self):
        assert truncate_line(AnsiLine.of("ab"), 10).plain == "ab"


class TestPad:
    def test_pad(self):
        assert pad_line(AnsiLine.of("ab"), 5).plain == "ab   "

    def test_pad_truncates(self):
        assert pad_line(AnsiLine.of("abcdef"), 3).plain == "abc"


class TestAnsiToRuns:
    def test_strip_ansi(self):
        assert strip_ansi("\033[38;5;45mhi\033[0m") == "hi"

    def test_visual_width(self):
        assert visual_width("\033[38;5;45mhi\033[0m") == 2

    def test_parse_sgr_basic(self):
        style, reset = parse_sgr_params("1;38;5;45")
        assert reset is False
        assert style.bold is True
        assert style.fg == 45

    def test_parse_sgr_reset(self):
        style, reset = parse_sgr_params("0")
        assert reset is True

    def test_parse_sgr_truecolor(self):
        style, _ = parse_sgr_params("38;2;10;20;30")
        assert style.fg == (10, 20, 30)

    def test_ansi_to_runs_splits(self):
        runs = ansi_to_runs("a\033[1mb\033[0mc")
        assert "".join(r.text for r in runs) == "abc"
        # 中段 run 加粗
        mid = [r for r in runs if r.text == "b"]
        assert mid and mid[0].style.bold is True

    def test_ansi_to_line(self):
        line = ansi_to_line("\033[38;5;45mx\033[0m")
        assert line.plain == "x"
        assert line.runs[0].style is not None
