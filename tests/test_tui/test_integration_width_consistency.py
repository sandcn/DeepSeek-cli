"""跨模块宽度一致性集成测试（步骤 13）。

修复背景（2026-08-15 H1-H3）：跨模块调用链完整性验证——本文件锁定
  - H1 双宽度函数（``wcswidth_simple`` / ``cjk_display_width``）在 wrap
    产出段上测量一致（模拟 committed 侧 wrap/cjk 与 live 侧 wcswidth
    对齐——含韩文/部首/假名/emoji/零宽/ANSI 混合行）；
  - H2 工具卡（``tool_card_lines``）混合字符行宽不变量（每行 <= width
    且内容完整）；
  - H3 diff 出口截断（``show_file_diff`` → ``TerminalWidthCache`` →
    ``render_diff`` → ``_write_diff_line``）窄终端不 wraparound。
"""

from __future__ import annotations

import difflib
from types import SimpleNamespace

from src.tui._width import wcswidth_simple
from src.renderer.ansi.helpers import (
    AnsiLine,
    ansi_to_line,
    strip_ansi,
    visual_width,
    wrap_line,
)
from src.tui.app.toolcard import tool_card_lines


# ── 用例 1：双宽度一致性集成（H1）──────────────────────────

#: 覆盖各区间表的混合文本（含 ANSI 样式 + 韩文/部首/假名/CJK/emoji/零宽）
_MIXED_TEXT = (
    "\x1b[31m가나다\x1b[0m あいうえお "
    "\x1b[1m中文测试\x1b[0m 🎉🚀 "
    "⼀⼆ \u200b\u00ad extra xxxxxx"
)


def test_wrap_line_segment_width_consistency_regression():
    """H1 集成：wrap_line（cjk 测宽）产出每段宽度 == wcswidth_simple 测宽。

    模拟 committed 侧（``wrap_line`` 用 cjk_display_width 决定断点）与
    live 侧（ink 用 wcswidth_simple）对同一行测量对齐——H1 区间表补齐后
    韩文/部首/假名等字符两侧宽度一致，行级 diff 宽度不变量不被破坏。
    """
    line = ansi_to_line(_MIXED_TEXT)
    for width in (10, 20, 40):
        for seg in wrap_line(line, width):
            w_cjk = seg.width          # committed 侧（cjk_display_width）
            w_ink = wcswidth_simple(seg.plain)  # live 侧（wcswidth_simple）
            assert w_cjk == w_ink, (
                f"wrap 段宽度不一致 (width={width}): cjk={w_cjk} ink={w_ink} "
                f"plain={seg.plain!r}"
            )


def test_ansi_line_plain_width_consistency_regression():
    """H1 集成：含 ANSI 混合行整行 cjk 宽度 == wcswidth_simple 宽度。"""
    line = ansi_to_line(_MIXED_TEXT)
    assert line.width == wcswidth_simple(line.plain), (
        f"整行宽度不一致: cjk={line.width} "
        f"ink={wcswidth_simple(line.plain)} plain={line.plain!r}"
    )


# ── 用例 2：工具卡行宽不变量（H2）──────────────────────────

def _make_mixed_block() -> SimpleNamespace:
    """构造含混合字符内容行的 ChatBlock 鸭子类型（无空格断点行）。"""
    # 60 列 = 가나다(6) + あいう(6) + 中文(4) + 🎉🚀(4) + 40 个 x
    text = "가나다あいう中文🎉🚀" + "x" * 40
    return SimpleNamespace(
        lines=[AnsiLine.of("标题"), AnsiLine.of(text)],
        closed=True,
        extra={
            "tool_status": "done",
            "tool_name": "bash",
            "tool_detail": "ls -la",
            "_bash_omitted_lines": 0,
            "_head_omitted_lines": 0,
        },
    )


def _content_text(line: list) -> str:
    """剥离行首竖线引导 run 后的内容文本。"""
    return "".join(r.text for r in line[1:])


def test_tool_card_width_invariant_mixed_regression():
    """H2 集成：混合字符长行每行总宽 <= width 且内容完整（拼接 == 原文）。"""
    block = _make_mixed_block()
    original = block.lines[1].plain
    for width in (20, 40, 60):
        lines = tool_card_lines(block, width)[1:]  # 跳标题行
        assert lines, "应有内容行"
        joined = ""
        for line in lines:
            total = sum(r.width for r in line)
            assert total <= width, f"width={width} 行宽 {total} > {width}"
            joined += _content_text(line)
        assert joined == original, (
            f"width={width} 内容丢失: 拼接 {len(joined)} != 原文 {len(original)}"
        )


def test_tool_card_ink_width_matches_runs_sum_regression():
    """H1 集成：工具卡行 wcswidth_simple 测宽 == StyledRun 宽度累加。"""
    block = _make_mixed_block()
    lines = tool_card_lines(block, 40)[1:]
    for line in lines:
        runs_sum = sum(r.width for r in line)
        plain = "".join(r.text for r in line)
        ink_w = wcswidth_simple(plain)
        assert runs_sum == ink_w, (
            f"runs 累加 {runs_sum} != wcswidth_simple {ink_w}: {plain!r}"
        )


# ── 用例 3：diff 出口截断集成（H3）──────────────────────────

class _Collector:
    """收集 write_line 调用的简单输出目标（与 render_diff_to_ansi 同型）。"""

    _target: list = []

    @classmethod
    def write_line(cls, text: str) -> None:
        cls._target.append(text)


def test_show_file_diff_truncates_to_terminal_width_regression(monkeypatch):
    """H3 集成：show_file_diff 窄终端（宽 30）输出每行 <= 30 列。

    调用链：show_file_diff → TerminalWidthCache.get_default().get_width()
    → render_diff(..., max_width=term_w) → _write_diff_line 出口截断——
    窄终端 diff 长行不 wraparound。
    """
    class _FakeTerminal:
        _w = 30

        @classmethod
        def get_default(cls):
            return cls()

        def get_width(self):
            return self._w

    monkeypatch.setattr("src.tui._screen.TerminalWidthCache", _FakeTerminal)
    from src.tui._diff_renderer import show_file_diff

    old = "line1\n" + "x" * 60 + "\nline3\nline4\nline5\nline6\n"
    new = "line1\n" + "y" * 60 + "\nline3\nline4\nline5\nline6\n"
    collected: list = []
    _Collector._target = collected
    show_file_diff("f.py", old, new, output_target=_Collector)
    assert collected, "应有 diff 输出"
    for line in collected:
        assert visual_width(strip_ansi(line)) <= 30, (
            f"超宽 {visual_width(strip_ansi(line))}: {line!r}"
        )
    # 长 add/del 行确实被截断（而非输出超宽行）
    text = "\n".join(strip_ansi(l) for l in collected)
    assert "y" * 60 not in text, "超长新增行未被截断（应截到 <= 30 列）"
