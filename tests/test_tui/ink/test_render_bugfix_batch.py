"""回归测试 — 渲染错乱/显示 bug 修复批次（BUG-70~72 + 单行契约收敛）。

覆盖：
  - BUG-70：wrap_runs_by_width width<=0 丢弃空行（与正宽/FrameBuilder 语义不一致）
  - BUG-71a：truncate_runs_start max_width=0 返回宽 1 省略号（行宽不变量破坏）
  - BUG-71b：truncate_runs_* 含换行文本保留字面 \\n（行级 diff 宽度不变量破坏）
  - BUG-72：_build_separator_line 行宽 width-1（右端缺 1 列，与状态栏不对齐）
  - 单行契约收敛：_format.single_line 单一真源（model/_subagent_render/subagent_panel）
"""

from __future__ import annotations

from src.tui.ink.output import StyledRun, Line
from src.tui.ink.helpers import (
    wrap_runs_by_width,
    truncate_runs,
    truncate_runs_ellipsis,
    truncate_runs_start,
    truncate_runs_middle,
    truncate_line,
)


class TestWrapZeroWidthEmptyLine:
    """BUG-70 — wrap_runs_by_width width<=0 空行语义。"""

    def test_zero_width_preserves_middle_empty_line(self):
        """``a\\n\\nb`` 在零宽分支保留中间空行（与正宽分支一致）。"""
        for w in (0, 10):
            lines = wrap_runs_by_width([StyledRun("a\n\nb")], w)
            assert [l.plain for l in lines] == ["a", "", "b"], (
                f"width={w} 应保留中间空行: {[l.plain for l in lines]}"
            )

    def test_zero_width_newline_at_start(self):
        """``\\na`` 在零宽分支产生开头空行（与正宽分支一致）。"""
        for w in (0, 10):
            lines = wrap_runs_by_width([StyledRun("\na")], w)
            assert [l.plain for l in lines] == ["", "a"], (
                f"width={w} 应保留开头空行: {[l.plain for l in lines]}"
            )

    def test_zero_width_newline_at_end(self):
        """``a\\n`` 尾部空行不额外产生（与正宽分支一致——尾部空行由 frame 末尾处理）。"""
        for w in (0, 10):
            lines = wrap_runs_by_width([StyledRun("a\n")], w)
            assert [l.plain for l in lines] == ["a"], (
                f"width={w} 尾部空行不额外产生: {[l.plain for l in lines]}"
            )

    def test_zero_width_single_text_unchanged(self):
        """纯文本零宽仍单行（既有契约 test_wrap_runs_no_width 锁定）。"""
        lines = wrap_runs_by_width([StyledRun("abc")], 0)
        assert len(lines) == 1
        assert lines[0].plain == "abc"


class TestTruncateRunsZeroWidth:
    """BUG-71a — truncate_runs_start max_width=0 行宽不变量。"""

    def test_start_zero_width_returns_empty(self):
        """truncate-start max_width=0 返回空列表（不返回宽 1 省略号）。"""
        out = truncate_runs_start([StyledRun("abc")], 0)
        assert out == [], f"max_width=0 应返回空，实际 {out!r}"
        assert sum(r.width for r in out) == 0

    def test_ellipsis_zero_width_returns_empty(self):
        """truncate-end max_width=0 返回空（对齐——既有行为锁定）。"""
        assert truncate_runs_ellipsis([StyledRun("abc")], 0) == []

    def test_middle_zero_width_returns_empty(self):
        """truncate-middle max_width=0 返回空（对齐——既有行为锁定）。"""
        assert truncate_runs_middle([StyledRun("abc")], 0) == []

    def test_start_zero_width_empty_runs(self):
        """空 runs + max_width=0 → 空。"""
        assert truncate_runs_start([], 0) == []


class TestTruncateRunsNewline:
    """BUG-71b — truncate_runs_* 含换行文本不保留字面 \\n（行宽不变量）。"""

    def _run_plain(self, out) -> str:
        return "".join(r.text for r in out)

    def test_ellipsis_newline_truncates_to_first_line(self):
        """含换行文本 truncate-end：只保留首逻辑行（不保留 \\n）。"""
        out = truncate_runs_ellipsis([StyledRun("hello\nworld")], 6)
        assert "\n" not in self._run_plain(out), f"不应含字面 \\n: {self._run_plain(out)!r}"
        assert self._run_plain(out) == "hello"

    def test_start_newline_truncates_to_first_line(self):
        """含换行文本 truncate-start：只保留首逻辑行（不保留 \\n）。"""
        out = truncate_runs_start([StyledRun("hello\nworld")], 6)
        assert "\n" not in self._run_plain(out), f"不应含字面 \\n: {self._run_plain(out)!r}"
        assert self._run_plain(out) == "hello"

    def test_middle_newline_truncates_to_first_line(self):
        """含换行文本 truncate-middle：只保留首逻辑行（不保留 \\n）。"""
        out = truncate_runs_middle([StyledRun("hello\nworld")], 6)
        assert "\n" not in self._run_plain(out), f"不应含字面 \\n: {self._run_plain(out)!r}"
        assert self._run_plain(out) == "hello"

    def test_truncate_runs_newline_no_literal_newline(self):
        """truncate_runs 含换行：只保留首逻辑行。"""
        out = truncate_runs([StyledRun("hello\nworld")], 6)
        assert "\n" not in self._run_plain(out)
        assert self._run_plain(out) == "hello"

    def test_truncate_line_newline_no_literal_newline(self):
        """truncate_line 含换行：只保留首逻辑行。"""
        out = truncate_line(Line.of("hello\nworld"), 6)
        assert "\n" not in out.plain, f"truncate_line 不应含字面 \\n: {out.plain!r}"
        assert out.plain == "hello"

    def test_no_newline_returns_same_plain(self):
        """无换行文本行为不变（回归保护）。"""
        out = truncate_runs_ellipsis([StyledRun("hello world")], 6)
        assert "\n" not in self._run_plain(out)
        assert self._run_plain(out).startswith("hello")

    def test_newline_within_width_returns_first_line(self):
        """含换行但首行未超宽：返回首行（不保留 \\n 与后续）。"""
        out = truncate_runs([StyledRun("hello\nworld")], 12)
        assert "\n" not in self._run_plain(out), f"不应含字面 \\n: {self._run_plain(out)!r}"
        assert self._run_plain(out) == "hello"


class TestSeparatorLineWidth:
    """BUG-72 — sep_line 行宽恒 = width（右端不缺口；_build_separator_line 遗留已移除）。"""

    @staticmethod
    def _build(width: int) -> Line:
        from src.tui.app._theme import sep_line
        from src.tui.core.style import Style
        content = Line.of(" CPU:12% · MEM:34%", Style(fg=45))
        return sep_line(width, content, False)

    def test_normal_width_full(self):
        """正常宽度：行宽 == width（修复前 width-1）。"""
        for w in (50, 80):
            line = self._build(w)
            assert line.width == w, f"width={w} 行宽应=width，实际 {line.width}"

    def test_narrow_width_not_overflow(self):
        """窄屏：行宽 ≤ width（截断后不溢出）。"""
        from src.tui.ink.fiber import Fiber
        from src.tui.ink.layout import LayoutBox
        from src.tui.app.input_area import _build_lines

        def _fiber(w):
            f = Fiber("host", "input-area")
            f.props = {"text": "", "status_active": False, "cpu": 12, "mem": 34}
            f.layout_box = LayoutBox(0, 0, w, 3)
            return f

        for w in (20, 15):
            lines = _build_lines(_fiber(w))
            assert all(l.width <= w for l in lines), (
                f"width={w} 行超宽: {[l.width for l in lines]}"
            )
            # 分隔线行（首/末）宽度 == width（满宽对齐 status_bar）
            assert lines[0].width == w, f"上分隔线应满宽: {lines[0].width} != {w}"
            # 2026-08-14：时间戳行（下分隔线）为倒数第 2 行；其后为新增主
            # Agent 运行模式行（同样满宽）。
            assert lines[-2].width == w, f"下分隔线应满宽: {lines[-2].width} != {w}"
            assert lines[-1].width == w, f"模式行应满宽: {lines[-1].width} != {w}"


class TestSingleLineConvergence:
    """单行契约收敛 — _format.single_line 单一真源。"""

    def test_format_single_line_escapes(self):
        """_format.single_line：\\n/\\r 转义为字面量。"""
        from src.tui._format import single_line
        assert single_line("") == ""
        assert single_line("a\nb") == "a\\nb"
        assert single_line("a\rb") == "a\\rb"
        assert single_line("a\r\nb") == "a\\r\\nb"

    def test_model_detail_delegates(self):
        """model._single_line_detail 委托 _format.single_line（行为一致）。"""
        from src.tui.app.model import _single_line_detail
        assert _single_line_detail("") == ""
        assert _single_line_detail("a\nb") == "a\\nb"
        assert _single_line_detail("a\rb") == "a\\rb"

    def test_subagent_render_delegates(self):
        """_subagent_render._single_line 委托 _format.single_line（行为一致）。"""
        from src.tui._subagent_render import _single_line
        assert _single_line("") == ""
        assert _single_line("a\nb") == "a\\nb"
        assert _single_line("a\rb") == "a\\rb"
