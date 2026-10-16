"""流式超长单行渲染性能回归（TUI / ink 层）。

覆盖本轮针对「超长单行流式」的 TUI 侧修复：

1. ``ink._runs_utils.wrap_runs_by_width`` 新增**等宽宽字符快路径**（整段字符
   显示宽度全为 2：CJK / 全角 / emoji——超长中文单行的常见形态）按固定步长
   切分；并把「片段索引表 + 切片发射」提取为 ``_span_slices`` /
   ``_emit_span_range`` 供 ASCII 快路径与等宽快路径共用。
2. ``ink.output.StyledRun.fast``——调用方已知显示宽度时跳过重算（超长活动行
   每帧重渲染尾部窗口，此前每个 run 都要重新逐字符测宽）。
3. ``ink.registry.active_hosts`` 结果缓存（热路径每帧上百次查询，此前每次
   重建 dict 并做 ``importlib`` 模块解析），注册/禁用/接管状态变化时失效。
4. ``app.apply.coalesce_commands``——同一渲染帧内相邻 CONTENT / REASONING
   增量命令合并（同一未闭合块每帧只刷新一次预览）。
5. ``ansi.helpers.AnsiLine.exceeds_width`` 与 chat_view 的接入（预览行是否
   需要换行的判定改为可提前退出）。
"""
from __future__ import annotations

import random

import pytest

from src.renderer.ansi.helpers import AnsiLine, Run, wrap_line
from src.tui.core.style import Style
from src.tui.ink import registry as ink_registry
from src.tui.ink._runs_utils import _emit_span_range, _span_slices, wrap_runs_by_width
from src.tui.ink.output import Line, StyledRun
from src.tui._width import wcswidth_simple


# ═══════════════════════════════════════════════════════════
# 参考实现（修复前通用路径算法）
# ═══════════════════════════════════════════════════════════


def _wrap_runs_reference(runs: list[StyledRun], max_width: int,
                         hard: bool = False) -> list[Line]:
    """逐字符 tuple 展开 + 贪心填充的参考实现（修复前算法）。"""
    items: list[tuple[str, Style | None, str | None]] = []
    for run in runs:
        for ch in run.text:
            items.append((ch, run.style, run.link))
    n = len(items)
    if n == 0:
        return []
    lines: list[Line] = []
    i = 0
    while i < n:
        j = i
        width = 0
        last_space = -1
        while j < n:
            ch = items[j][0]
            if ch == "\n":
                break
            if ch == " " and not hard:
                last_space = j
            cw = wcswidth_simple(ch)
            if width + cw > max_width and j > i:
                break
            width += cw
            j += 1
        if j == i:
            if items[i][0] == "\n":
                lines.append(Line())
                i += 1
                continue
            end = i + 1
            next_i = i + 1
        else:
            # ★ 行首单字符超宽（max_width < 该字符宽度）：跳过该字符不产出
            #   超宽行（与 ``wrap_runs_by_width`` 的行宽不变量语义一致）。
            if j == i + 1 and width > max_width:
                i += 1
                continue
            if j < n and items[j][0] == "\n":
                end = j
                next_i = j + 1
            elif j < n and last_space > i and not hard:
                end = last_space
                next_i = last_space + 1
            else:
                end = j
                next_i = j
        ln = Line()
        for k in range(i, end):
            ch, st, lk = items[k]
            ln.append(ch, st, lk)
        if ln.runs:
            lines.append(ln)
        i = next_i
    return lines


def _lines_plain(lines: list[Line]) -> list[str]:
    return ["".join(r.text for r in ln.runs) for ln in lines]


def _lines_runs(lines: list[Line]):
    return [[(r.text, r.style, r.link) for r in ln.runs] for ln in lines]


_WRAP_RUN_CASES = [
    [StyledRun("x" * 300, None)],
    [StyledRun("hello world " * 20, None)],
    [StyledRun("\u4e2d\u6587\u5185\u5bb9" * 40, None)],
    [StyledRun("\u4e2d", Style(fg=1)), StyledRun("\u6587\u5185\u5bb9" * 20, Style(fg=2))],
    [StyledRun("emoji \U0001f600\U0001f601 " * 8, None)],
    [StyledRun("a\nb\n\nc", None)],
    [StyledRun("mixed \u4e2d\u6587 abc", Style(fg=3))],
    [StyledRun("caf\u00e9 r\u00e9sum\u00e9 " * 8, None)],
]

_WRAP_RUN_WIDTHS = [1, 2, 3, 5, 8, 12, 40, 120, 400]


class TestWrapRunsEquivalence:
    def test_equivalent_to_reference(self):
        for runs in _WRAP_RUN_CASES:
            for width in _WRAP_RUN_WIDTHS:
                for hard in (False, True):
                    got = wrap_runs_by_width(runs, width, hard=hard)
                    exp = _wrap_runs_reference(runs, width, hard=hard)
                    assert _lines_plain(got) == _lines_plain(exp), (
                        f"runs={runs!r} width={width} hard={hard}")
                    assert _lines_runs(got) == _lines_runs(exp), (
                        f"runs={runs!r} width={width} hard={hard}")

    def test_uniform_cjk_fast_path(self):
        """纯 CJK 行按固定步长切分（宽 2 → 每行 width//2 字符）。"""
        runs = [StyledRun("\u4e2d\u6587" * 30, None)]
        lines = wrap_runs_by_width(runs, 10)
        assert [len("".join(r.text for r in ln.runs)) for ln in lines[:3]] == [5, 5, 5]
        assert all(ln.width <= 10 for ln in lines)

    def test_random_fuzz_equivalence(self):
        rnd = random.Random(20261008)
        alphabet = ["a", "b", " ", "\u4e2d", "\u6587", "\U0001f600", "\u00e9", "\n", "-"]
        for _ in range(120):
            text = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 40)))
            runs = [StyledRun(text, None)]
            width = rnd.randint(1, 15)
            got = wrap_runs_by_width(runs, width)
            exp = _wrap_runs_reference(runs, width)
            assert _lines_plain(got) == _lines_plain(exp), (text, width)
            assert _lines_runs(got) == _lines_runs(exp), (text, width)

    def test_span_helpers_roundtrip(self):
        runs = [StyledRun("ab", Style(fg=1)), StyledRun("\u4e2d\u6587", Style(fg=2)),
                StyledRun("cd", None)]
        text, spans = _span_slices(runs)
        assert text == "ab\u4e2d\u6587cd"
        ln = Line()
        _emit_span_range(ln, text, spans, 1, 5)
        assert "".join(r.text for r in ln.runs) == "b\u4e2d\u6587c"
        assert [(r.style) for r in ln.runs] == [Style(fg=1), Style(fg=2), None]


# ═══════════════════════════════════════════════════════════
# StyledRun.fast
# ═══════════════════════════════════════════════════════════


class TestStyledRunFast:
    def test_fast_matches_normal(self):
        for text in ("abc", "\u4e2d\u6587", "a\u4e2db", ""):
            normal = StyledRun(text, Style(fg=3))
            fast = StyledRun.fast(text, Style(fg=3), None, normal.width)
            assert fast.text == normal.text
            assert fast.style == normal.style
            assert fast.width == normal.width
            assert fast.render() == normal.render()

    def test_fast_falls_back_without_width(self):
        run = StyledRun.fast("\u4e2d\u6587", None, None, None)
        assert run.width == wcswidth_simple("\u4e2d\u6587")

    def test_fast_with_link(self):
        run = StyledRun.fast("http://a.b", None, "http://a.b", 10)
        assert run.link == "http://a.b"
        assert "http://a.b" in run.render()


# ═══════════════════════════════════════════════════════════
# registry.active_hosts 缓存
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def clean_registry():
    ink_registry.reset()
    yield
    ink_registry.reset()


class TestActiveHostsCache:
    def test_cached_object_identity(self, clean_registry):
        a = ink_registry.active_hosts()
        b = ink_registry.active_hosts()
        assert a is b  # 热路径零重建

    def test_invalidated_on_register(self, clean_registry):
        before = ink_registry.active_hosts()
        undo = ink_registry.register_builtin_host("static-lines")
        after = ink_registry.active_hosts()
        assert after is not before
        undo()
        after2 = ink_registry.active_hosts()
        assert after2 is not after

    def test_invalidated_on_disable(self, clean_registry):
        before = ink_registry.active_hosts()
        undo = ink_registry.disable_builtin_hosts(["static-lines"])
        after = ink_registry.active_hosts()
        assert after is not before
        assert "static-lines" not in after
        undo()
        assert "static-lines" in ink_registry.active_hosts()

    def test_invalidated_on_clear(self, clean_registry):
        before = ink_registry.active_hosts()
        ink_registry.clear()
        assert ink_registry.active_hosts() is not before

    def test_get_host_uses_cache(self, clean_registry):
        h1 = ink_registry.get_host("static-lines")
        h2 = ink_registry.get_host("static-lines")
        assert h1 == h2


# ═══════════════════════════════════════════════════════════
# coalesce_commands
# ═══════════════════════════════════════════════════════════


class TestCoalesceCommands:
    def test_merge_adjacent_content(self):
        from src.tui.app.apply import coalesce_commands
        from src.tui._const import ContentCmd
        out = coalesce_commands([ContentCmd(text="a"), ContentCmd(text="b"),
                                 ContentCmd(text="c")])
        assert len(out) == 1
        assert out[0].text == "abc"

    def test_merge_adjacent_reasoning(self):
        from src.tui.app.apply import coalesce_commands
        from src.tui._const import ReasoningCmd
        out = coalesce_commands([ReasoningCmd(text="x"), ReasoningCmd(text="y")])
        assert len(out) == 1
        assert out[0].text == "xy"

    def test_does_not_merge_across_other_commands(self):
        from src.tui.app.apply import coalesce_commands
        from src.tui._const import ContentCmd, PhaseDoneCmd
        cmds = [ContentCmd(text="a"), PhaseDoneCmd(phase="content"),
                ContentCmd(text="b")]
        out = coalesce_commands(cmds)
        assert [type(c).__name__ for c in out] == [
            "ContentCmd", "PhaseDoneCmd", "ContentCmd"]
        assert [getattr(c, "text", "") for c in out] == ["a", "", "b"]

    def test_does_not_merge_different_channels(self):
        from src.tui.app.apply import coalesce_commands
        from src.tui._const import ContentCmd, ReasoningCmd
        out = coalesce_commands([ContentCmd(text="a"), ReasoningCmd(text="b"),
                                 ContentCmd(text="c")])
        assert [type(c).__name__ for c in out] == [
            "ContentCmd", "ReasoningCmd", "ContentCmd"]

    def test_preserves_command_order(self):
        from src.tui.app.apply import coalesce_commands
        from src.tui._const import ContentCmd, ToolOpenCmd, ToolCloseCmd
        cmds = [ToolOpenCmd(tool_name="t", tool_id="1"),
                ContentCmd(text="a"), ContentCmd(text="b"),
                ToolCloseCmd(tool_id="1")]
        out = coalesce_commands(cmds)
        assert [type(c).__name__ for c in out] == [
            "ToolOpenCmd", "ContentCmd", "ToolCloseCmd"]
        assert out[1].text == "ab"

    def test_equivalent_model_result(self):
        """合并后应用的模型状态与逐条应用一致（内容块行内容）。"""
        from src.tui.app.apply import apply_cmd, coalesce_commands
        from src.tui.app.model import AppModel
        from src.tui._const import ContentCmd

        cmds = [ContentCmd(text="hello "), ContentCmd(text="world\n"),
                ContentCmd(text="second line")]
        m1 = AppModel()
        for c in cmds:
            apply_cmd(m1, c)
        m2 = AppModel()
        for c in coalesce_commands(cmds):
            apply_cmd(m2, c)
        b1 = m1.blocks[m1.content_block_index]
        b2 = m2.blocks[m2.content_block_index]
        assert [ln.plain for ln in b1.lines] == [ln.plain for ln in b2.lines]
        assert [ln.plain for ln in b1.preview_lines] == [
            ln.plain for ln in b2.preview_lines]


# ═══════════════════════════════════════════════════════════
# chat_view 预览行换行判定
# ═══════════════════════════════════════════════════════════


class TestPreviewWrapDecision:
    def test_exceeds_width_matches_width(self):
        for text in ("short", "\u4e2d\u6587" * 100, "x" * 500):
            line = AnsiLine([Run(text, None)])
            for limit in (10, 40, 120, 1000):
                assert line.exceeds_width(limit) == (line.width > limit)

    def test_chat_view_uses_exceeds_width(self):
        import inspect
        from src.tui.app import chat_view
        src = inspect.getsource(chat_view._block_styled_lines)
        assert "exceeds_width" in src


# ═══════════════════════════════════════════════════════════
# 超长单行性能边界（防超线性退化回归；阈值宽松 20x+ 余量）
# ═══════════════════════════════════════════════════════════


def _elapsed(fn, repeats: int = 5) -> float:
    import time
    fn()  # 预热（缓存填充）
    t0 = time.perf_counter()
    for _ in range(repeats):
        fn()
    return (time.perf_counter() - t0) / repeats


class TestLongLinePerfBudget:
    def test_renderer_wrap_line_cjk_budget(self):
        line = AnsiLine([Run("\u4e2d\u6587\u5185\u5bb9" * 1024, None)])
        assert _elapsed(lambda: wrap_line(line, 120)) < 0.03

    def test_renderer_wrap_line_ascii_budget(self):
        line = AnsiLine([Run("lorem ipsum dolor sit amet " * 250, None)])
        assert _elapsed(lambda: wrap_line(line, 120)) < 0.03

    def test_tui_wrap_runs_cjk_budget(self):
        runs = [StyledRun("\u4e2d\u6587\u5185\u5bb9" * 1024, None)]
        assert _elapsed(lambda: wrap_runs_by_width(runs, 120)) < 0.05

    def test_inline_parse_long_plain_budget(self):
        from src.renderer.ansi.inline import render_inline
        text = "lorem ipsum dolor sit amet " * 400
        assert _elapsed(lambda: render_inline(text)) < 0.02

    def test_string_width_cjk_budget(self):
        from src._text_width import string_width
        text = "\u4e2d\u6587\u5185\u5bb9" * 2048
        assert _elapsed(lambda: string_width(text)) < 0.03

    def test_stream_renderer_long_line_linear(self):
        """超长单行流式渲染随帧数的总耗时受控（非平方退化）。"""
        from src.renderer.ansi import AnsiStreamRenderer

        def _run(chunks: int) -> float:
            r = AnsiStreamRenderer(width=120)
            import time
            t0 = time.perf_counter()
            for _ in range(chunks):
                r.write("x" * 200)
                r.take_preview_lines()
            return time.perf_counter() - t0

        assert _run(400) < 1.5

