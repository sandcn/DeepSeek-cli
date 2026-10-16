"""制表符显示错乱修复回归测试（2026-10-05）。

bug：工具输出含制表符（如 ``git remote -v`` 的 ``origin\\thttps://…``）时，
``\\t`` 是控制字符（显示宽度计 0），但真实终端把它当 HT 跳到下一个 tab stop
（默认每 8 列补空格）——「计算宽度 < 实际渲染宽度」。结合「工具卡整行占满
终端宽度」的填充（按计算宽度补空格到终端列宽），整行实际渲染宽度超过终端
列宽 → 终端自动换行 → 后续行光标定位整体错位（屏幕多出空行、状态栏串行、
行内容被吃掉前若干列）。

修复：文本进入渲染模型时展开 ``\\t`` 为空格、剔除 ``\\r``（行渲染模型的
最小文本单元：ink ``StyledRun`` / renderer ``Run``），此后宽度计算与实际
渲染恒一致。
"""

from __future__ import annotations

import io

import pytest

from src.tui._width import expand_tabs, wcswidth_simple
from src.renderer._utils import expand_tabs as r_expand_tabs
from src.renderer._utils import cjk_display_width
from src.tui.ink import Line, StyledRun, Frame
from src.tui.ink.renderer import InkRenderer

try:
    import pyte
except ImportError:  # pragma: no cover - 环境未安装 pyte 时跳过终端模拟用例
    pyte = None


# ═══════════════════════════════════════════════════════════
# expand_tabs — 纯函数
# ═══════════════════════════════════════════════════════════


class TestExpandTabs:
    def test_no_tab_no_cr_returns_same_object(self):
        s = "plain text"
        assert expand_tabs(s) is s

    def test_basic_tab_stop(self):
        # 'ab' 占列 0..1，tab 跳到列 8 → 补 6 空格
        assert expand_tabs("ab\tc") == "ab      c"

    def test_start_col_alignment(self):
        # 起始列 2：'ab' 占列 2..3，tab 跳到列 8 → 补 4
        assert expand_tabs("ab\tc", start_col=2) == "ab    c"

    def test_consecutive_tabs(self):
        assert expand_tabs("a\t\tb") == "a" + " " * 7 + " " * 8 + "b"

    def test_wide_char_column(self):
        # 宽字符占 2 列：'中' 在列 0..1，tab 跳到列 8 → 补 6
        out = expand_tabs("中\tx")
        assert out == "中      x"
        assert wcswidth_simple(out) == 9

    def test_carriage_return_removed(self):
        assert expand_tabs("a\rb") == "ab"
        assert "\r" not in expand_tabs("progress\rdone")

    def test_newline_resets_column(self):
        # 换行后列基准重置：'\t' 在每行开头都补 8
        assert expand_tabs("x\n\tb") == "x\n" + " " * 8 + "b"

    def test_renderer_variant_matches_ink(self):
        for s in ["ab\tc", "中\tx", "a\rb", "no tab", "\tstart", "a\t\tb"]:
            assert r_expand_tabs(s) == expand_tabs(s)


# ═══════════════════════════════════════════════════════════
# 渲染文本最小单元 — StyledRun / Run
# ═══════════════════════════════════════════════════════════


class TestStyledRunTab:
    def test_tab_expanded_and_width_matches_render(self):
        run = StyledRun("ab\tc")
        assert "\t" not in run.text
        assert run.text == "ab      c"
        assert run.width == wcswidth_simple(run.render())

    def test_plain_text_untouched(self):
        run = StyledRun("plain")
        assert run.text == "plain"
        assert run.width == 5

    def test_carriage_return_removed(self):
        assert StyledRun("a\rb").text == "ab"


class TestRenderRunTab:
    def test_tab_expanded(self):
        from src.renderer.ansi.helpers import Run
        run = Run("ab\tc")
        assert run.text == "ab      c"
        assert run.width == cjk_display_width(run.text) == 9

    def test_carriage_return_removed(self):
        from src.renderer.ansi.helpers import Run
        assert Run("a\rb").text == "ab"


class TestLineWidthConsistency:
    def test_line_append_width_matches_render(self):
        line = Line()
        line.append("a\tb")
        assert "\t" not in line.render()
        assert line.width == wcswidth_simple(line.render())

    def test_line_append_merge_width_matches_render(self):
        # 与已有 run 同 style → 走 merge 分支（整串重建 StyledRun）
        line = Line([StyledRun("head", None)])
        line.append("a\tb", None)
        assert "\t" not in line.render()
        assert line.width == wcswidth_simple(line.render())

    def test_ansi_line_width_consistency(self):
        from src.renderer.ansi.helpers import AnsiLine
        line = AnsiLine.of("a\tb")
        assert "\t" not in line.plain
        assert line.width == cjk_display_width(line.plain)


class TestWrapWithTab:
    def test_wrap_line_respects_width(self):
        from src.renderer.ansi.helpers import AnsiLine, wrap_line
        line = AnsiLine.of("x" * 30 + "\t" + "y" * 20)
        for seg in wrap_line(line, 40):
            assert seg.width <= 40, f"段超宽: {seg.width}"

    def test_wrap_runs_by_width_respects_width(self):
        from src.tui.ink._runs_utils import wrap_runs_by_width
        runs = [StyledRun("x" * 30 + "\t" + "y" * 20, None)]
        for seg in wrap_runs_by_width(runs, 40):
            assert seg.width <= 40


# ═══════════════════════════════════════════════════════════
# 端到端 — 工具卡含 tab 的行不再触发终端自动换行
# ═══════════════════════════════════════════════════════════


def _build_model_with_tab_tool_output():
    from src.tui.app.model import AppModel
    from src.tui.app.toolcard import tool_card_lines

    model = AppModel()
    model.width = 80
    for i in range(8):
        model.committed_lines.append(Line([StyledRun(f"history {i}", None)]))
    block = model.open_tool_box("t1", "bash", "git remote -v")
    for seg in [
        "-----REMOTE-----",
        "origin\thttps://github.com/sandcn/DeepSeek-cli.git (fetch)",
        "origin\thttps://github.com/sandcn/DeepSeek-cli.git (push)",
    ]:
        model.append_tool_output("t1", seg + "\n")
    for runs in tool_card_lines(block, 80, 0):
        model.committed_lines.append(Line(list(runs)))
    model.committed_lines.append(Line([StyledRun("", None)]))
    return model


def _frame_from_model(model, status: str) -> Frame:
    lines = [Line([StyledRun("DEEPSEEK TITLE", None)])]
    lines.extend(model.committed_lines)
    lines.append(Line([StyledRun(status, None)]))
    lines.append(Line([StyledRun("> ", None)]))
    return Frame(lines)


class TestToolCardTabNoCorruption:
    def test_every_line_within_width_and_no_tab(self):
        model = _build_model_with_tab_tool_output()
        for line in model.committed_lines:
            assert "\t" not in line.render(), f"行残留制表符: {line.plain!r}"
            # 满宽填充后计算宽度 <= 终端宽度（不超宽 → 不触发终端换行）
            assert line.width <= 80, f"行宽超终端: {line.width}"

    @pytest.mark.skipif(pyte is None, reason="pyte 未安装（终端模拟依赖）")
    def test_tool_output_tab_no_wrap_offset(self):
        """含 tab 的工具输出行渲染后：push 行紧接 fetch 行（无 wrap 残留空行）。"""
        model = _build_model_with_tab_tool_output()
        renderer = InkRenderer(stream=io.StringIO(), height=24)
        screen = pyte.Screen(80, 24)
        stream = pyte.Stream(screen)

        renderer.render(_frame_from_model(model, "status A 1.0s"))
        stream.feed(renderer._stream.getvalue())

        disp = list(screen.display)
        fetch_idx = next(i for i, l in enumerate(disp) if "(fetch)" in l)
        assert "(push)" in disp[fetch_idx + 1], (
            f"fetch 行后应为 push 行，实际: {disp[fetch_idx + 1]!r}\n"
            + "\n".join(disp)
        )

    @pytest.mark.skipif(pyte is None, reason="pyte 未安装（终端模拟依赖）")
    def test_status_line_incremental_frame_keeps_layout(self):
        """状态栏增量更新帧后布局不错位（fetch/push 相邻、状态栏唯一）。"""
        model = _build_model_with_tab_tool_output()
        renderer = InkRenderer(stream=io.StringIO(), height=24)
        screen = pyte.Screen(80, 24)
        stream = pyte.Stream(screen)

        renderer.render(_frame_from_model(model, "status A 1.0s"))
        stream.feed(renderer._stream.getvalue())

        out_before = len(renderer._stream.getvalue())
        renderer.render(_frame_from_model(model, "status B 0.6s"))
        stream.feed(renderer._stream.getvalue()[out_before:])

        disp = list(screen.display)
        fetch_idx = next(i for i, l in enumerate(disp) if "(fetch)" in l)
        assert "(push)" in disp[fetch_idx + 1]
        assert any("status B 0.6s" in l for l in disp)
        assert not any("status A 1.0s" in l for l in disp)
