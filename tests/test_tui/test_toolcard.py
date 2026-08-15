"""工具卡内容行 wrap 预算修正测试（H2）。

修复背景（2026-08-15 H2）：``tool_card_lines`` 内容行按总宽
``wrap_line(ansi_line, width)`` 换行，但每段显示预算仅 ``width-2``
（竖线引导 ``│ `` 占 2 列）→ 长行跨段时每段末尾 2 列被
``truncate_runs(seg_runs, width-2)`` 丢弃。修复：wrap 宽度改用
``content_w = width - guide_w``——每段 + 竖线后恰为 width，不丢内容。

本测试锁定：ASCII/CJK 长行内容完整（拼接文本 == 原文）、每行宽 <= width、
width=0/1 防御分支不变。
"""

from __future__ import annotations

from types import SimpleNamespace

from src.renderer.ansi.helpers import AnsiLine
from src.tui.app.toolcard import tool_card_lines


def _make_block(content_text: str) -> SimpleNamespace:
    """构造最小 ChatBlock 鸭子类型（lines/closed/extra）。

    lines[0] 为模型层标题行（tool_card_lines start=0 时跳过），lines[1:]
    为内容行。
    """
    return SimpleNamespace(
        lines=[AnsiLine.of("标题"), AnsiLine.of(content_text)],
        closed=True,
        extra={
            "tool_status": "done",
            "tool_name": "bash",
            "tool_detail": "ls -la",
            "_bash_omitted_lines": 0,
            "_head_omitted_lines": 0,
        },
    )


def _body_lines(lines: list) -> list:
    """内容行（跳过标题行——tool_card_lines start=0 输出首行为标题行）。"""
    return lines[1:]


def _content_text(line: list) -> str:
    """剥离行首竖线引导 run 后的内容文本。"""
    return "".join(r.text for r in line[1:])


def test_ascii_long_line_no_loss_regression():
    """H2 ASCII 长行（无空格断点）wrap 后内容完整、每行宽 <= width。"""
    text = "x" * 80
    block = _make_block(text)
    width = 40
    lines = _body_lines(tool_card_lines(block, width))
    for line in lines:
        total = sum(r.width for r in line)
        assert total <= width, f"行宽 {total} > {width}"
    joined = "".join(_content_text(line) for line in lines)
    assert joined == text, f"内容丢失: 拼接 {len(joined)} != 原文 {len(text)}"


def test_cjk_long_line_no_loss_regression():
    """H2 含 CJK 长行 wrap 后内容完整（每字符宽 2，不丢字符）、行宽 <= width。"""
    text = "中" * 40
    block = _make_block(text)
    width = 40
    lines = _body_lines(tool_card_lines(block, width))
    for line in lines:
        total = sum(r.width for r in line)
        assert total <= width, f"行宽 {total} > {width}"
    joined = "".join(_content_text(line) for line in lines)
    assert joined == text, f"内容丢失: 拼接 {len(joined)} != 原文 {len(text)}"


def test_width_one_guide_only_regression():
    """H2 width=1 极端窄屏：内容行仅竖线（1 列）。"""
    block = _make_block("hello world")
    lines = _body_lines(tool_card_lines(block, 1))
    assert lines, "width=1 应有内容行（仅竖线）"
    for line in lines:
        # 每行 = [StyledRun("│", ...)]（width=1 时 guide_text="│" 占 1 列）
        assert sum(r.width for r in line) == 1, f"width=1 行宽 {sum(r.width for r in line)}"


def test_width_zero_bare_line_regression():
    """H2 width=0 无宽度防御：内容行裸行不截断（无竖线引导）。"""
    text = "hello world"
    block = _make_block(text)
    lines = _body_lines(tool_card_lines(block, 0))
    joined = "".join("".join(r.text for r in line) for line in lines)
    assert joined == text, "width=0 应原样输出不截断"
