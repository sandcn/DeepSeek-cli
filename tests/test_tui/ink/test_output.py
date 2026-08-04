"""测试 ink/output.py — StyledRun / Line / Frame / FrameBuilder。"""

from __future__ import annotations

from src.tui.core.style import Style
from src.tui.ink.output import StyledRun, Line, Frame, FrameBuilder
from src.tui.ink.helpers import strip_ansi, wrap_runs_by_width, truncate_line, pad_line


class TestStyledRun:
    """StyledRun 渲染。"""

    def test_no_style_plain(self):
        run = StyledRun("hello")
        assert run.render() == "hello"

    def test_with_style(self):
        style = Style(fg=45, bold=True)
        run = StyledRun("hi", style)
        assert run.render() == "\033[1m\033[38;5;45mhi\033[0m"

    def test_width_cjk(self):
        assert StyledRun("abc").width == 3
        assert StyledRun("中文").width == 4


class TestLine:
    """Line 行为。"""

    def test_empty(self):
        line = Line()
        assert line.render() == ""
        assert line.width == 0

    def test_of(self):
        line = Line.of("x", Style(fg=45))
        assert line.render() == "\033[38;5;45mx\033[0m"

    def test_append_merges_same_style(self):
        line = Line()
        line.append("a", None)
        line.append("b", None)
        assert len(line.runs) == 1
        assert line.plain == "ab"

    def test_append_keeps_diff_style(self):
        line = Line()
        line.append("a", Style(fg=45))
        line.append("b", Style(fg=46))
        assert len(line.runs) == 2

    def test_append_run(self):
        line = Line()
        line.append_run(StyledRun("x", None))
        assert line.plain == "x"

    def test_width(self):
        line = Line()
        line.append("ab")
        line.append("中")
        assert line.width == 4

    def test_width_cache_incremental_append(self):
        """宽度惰性缓存 + append 增量维护（PERF：免重复 wcswidth_simple）。"""
        line = Line()
        assert line._w is None
        line.append("abc")
        assert line._w is None  # 未访问 width 前不计算
        assert line.width == 3
        assert line._w == 3     # 首次访问后缓存
        line.append("中")       # 增量维护（+2）
        assert line._w == 5
        assert line.width == 5

    def test_width_cache_merge_same_style(self):
        """同 style 合并分支的增量宽度：替换末 run（新宽 = 旧宽 + 追加宽）。"""
        line = Line.of("ab")
        assert line.width == 2
        assert line._w == 2
        line.append("cd", None)  # 同 style 合并
        assert line.plain == "abcd"
        assert line.width == 4   # 增量正确（合并后总宽 4）

    def test_width_cache_clone_preserves(self):
        """clone 复制宽度缓存（runs 未变，宽度相同）；副本 append 独立。"""
        line = Line.of("中文")
        assert line.width == 4
        clone = line.clone()
        assert clone.width == 4
        assert clone._w == 4
        clone.append("a")
        assert clone.width == 5
        assert line.width == 4  # 原行不受影响

    def test_styled_run_width_cached(self):
        """StyledRun 宽度构造期缓存（frozen 不可变安全）。"""
        from src.tui.ink.output import StyledRun as SR
        r = SR("中文")
        assert r.width == 4
        # eq/hash 不受缓存字段影响（compare=False）
        assert r == SR("中文")
        assert SR("a") != SR("b")
        assert hash(SR("ab")) == hash(SR("ab"))
        # repr 不显示缓存字段
        assert "width" not in repr(SR("x")) or "width=1" not in repr(SR("x"))
        assert SR("x").width == 1

    def test_clone_independent(self):
        line = Line.of("x")
        clone = line.clone()
        clone.append("y")
        assert line.plain == "x"
        assert clone.plain == "xy"


class TestFrame:
    """Frame 整帧渲染。"""

    def test_empty(self):
        f = Frame()
        assert f.height == 0
        assert f.to_ansi() == ""

    def test_to_ansi(self):
        f = Frame([Line.of("a"), Line.of("b")])
        assert f.to_ansi() == "a\nb\n"

    def test_height(self):
        f = Frame([Line.of("a"), Line.of("b"), Line.of("c")])
        assert f.height == 3
        assert f.render_line(1) == "b"


class TestFrameBuilder:
    """FrameBuilder 换行。"""

    def test_no_width_no_wrap(self):
        fb = FrameBuilder(0)
        fb.append("abcdef")
        f = fb.build()
        assert f.height == 1
        assert f.lines[0].plain == "abcdef"

    def test_wrap_by_width(self):
        fb = FrameBuilder(3)
        fb.append("abcdefgh")
        f = fb.build()
        # "abc" / "def" / "gh"
        assert f.height == 3
        assert [l.plain for l in f.lines] == ["abc", "def", "gh"]

    def test_wrap_cjk_never_splits_wide_char(self):
        fb = FrameBuilder(3)
        fb.append("ab中x")
        f = fb.build()
        # "ab" / "中x"
        assert f.height == 2
        assert [l.plain for l in f.lines] == ["ab", "中x"]

    def test_newline_force(self):
        fb = FrameBuilder(0)
        fb.append("a")
        fb.newline()
        fb.append("b")
        f = fb.build()
        assert f.height == 2
        assert [l.plain for l in f.lines] == ["a", "b"]

    def test_empty_lines_kept(self):
        fb = FrameBuilder(0)
        fb.newline()  # 空行
        fb.append("x")
        f = fb.build()
        assert f.height == 2


class TestHelpers:
    """helpers 工具。"""

    def test_strip_ansi(self):
        assert strip_ansi("\033[38;5;45mhi\033[0m") == "hi"

    def test_wrap_runs_by_width(self):
        runs = [StyledRun("abcdef", None)]
        lines = wrap_runs_by_width(runs, 3)
        assert [l.plain for l in lines] == ["abc", "def"]

    def test_wrap_runs_no_width(self):
        runs = [StyledRun("abc", None)]
        lines = wrap_runs_by_width(runs, 0)
        assert len(lines) == 1
        assert lines[0].plain == "abc"

    def test_truncate_line(self):
        line = Line.of("abcdef")
        out = truncate_line(line, 4)
        assert out.plain == "abcd"

    def test_truncate_line_wide_char_boundary(self):
        line = Line.of("ab中")
        out = truncate_line(line, 3)
        assert out.plain == "ab"

    def test_truncate_line_within_width(self):
        line = Line.of("abc")
        out = truncate_line(line, 10)
        assert out.plain == "abc"

    def test_pad_line(self):
        line = Line.of("ab")
        out = pad_line(line, 5)
        assert out.plain == "ab   "
        assert out.width == 5

    def test_pad_line_truncates(self):
        line = Line.of("abcdef")
        out = pad_line(line, 3)
        assert out.plain == "abc"
