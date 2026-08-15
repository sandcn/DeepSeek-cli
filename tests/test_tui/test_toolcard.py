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
    """构造最小 ChatBlock 鸭子类型（lines/closed/extra/tool_collapsed）。

    lines[0] 为模型层标题行（tool_card_lines start=0 时跳过），lines[1:]
    为内容行。
    """
    return SimpleNamespace(
        lines=[AnsiLine.of("标题"), AnsiLine.of(content_text)],
        closed=True,
        tool_collapsed=False,
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


# ── 折叠渲染（2026-08-15 用户需求：工具完成后自动折叠为单行） ─────

def _make_fold_block() -> SimpleNamespace:
    """构造已关闭且带折叠标志的工具块（多内容行）。"""
    return SimpleNamespace(
        lines=[
            AnsiLine.of("标题"),
            AnsiLine.of("内容-1"),
            AnsiLine.of("内容-2"),
        ],
        closed=True,
        tool_collapsed=True,
        extra={
            "tool_status": "done",
            "tool_name": "bash",
            "tool_detail": "ls -la",
            "_bash_omitted_lines": 0,
            "_head_omitted_lines": 0,
        },
    )


def test_collapsed_returns_title_only():
    """折叠工具卡 start=0：只返回标题行（状态图标+工具名+参数），无内容行。"""
    block = _make_fold_block()
    lines = tool_card_lines(block, 40)
    assert len(lines) == 1, f"折叠后应只有标题行，实际 {len(lines)} 行"
    title = "".join(r.text for r in lines[0])
    assert title.startswith("✔ "), f"标题应含完成图标: {title!r}"
    assert "Bash" in title and "ls -la" in title


def test_collapsed_tail_returns_empty():
    """折叠工具卡 start>0（未提交尾）：无内容行可显示，返回空列表。"""
    block = _make_fold_block()
    assert tool_card_lines(block, 40, 1, None) == []
    assert tool_card_lines(block, 40, 1, 2) == []


def test_expanded_returns_full_content():
    """展开工具卡（tool_collapsed=False）：标题行 + 全部内容行。"""
    block = _make_fold_block()
    block.tool_collapsed = False
    lines = tool_card_lines(block, 40)
    assert len(lines) == 3, f"展开后应有标题+2内容，实际 {len(lines)} 行"
    body = "".join(
        "".join(r.text for r in row)
        for row in _body_lines(lines)
    )
    assert "内容-1" in body and "内容-2" in body


def test_collapse_switch_invalidates_frame_cache():
    """折叠状态切换后 frame 缓存失效（key 含 collapsed 标志）。"""
    block = _make_fold_block()
    block.tool_collapsed = False
    expanded = tool_card_lines(block, 40)
    assert len(expanded) == 3
    block.tool_collapsed = True
    collapsed = tool_card_lines(block, 40)
    assert len(collapsed) == 1, "折叠状态切换后应重建为仅标题行"
    assert block._tool_card_frame_cache[0][3] is True, "frame key 应含 collapsed 标志"


def test_collapsed_running_icon_still_render():
    """折叠仅对已关闭块生效——开放块（未关闭）即使标记折叠仍渲染标题+内容。"""
    block = SimpleNamespace(
        lines=[AnsiLine.of("标题"), AnsiLine.of("内容")],
        closed=False,
        tool_collapsed=True,
        extra={
            "tool_status": "running",
            "tool_name": "bash",
            "tool_detail": "watch",
            "_bash_omitted_lines": 0,
            "_head_omitted_lines": 0,
        },
    )
    lines = tool_card_lines(block, 40)
    assert len(lines) == 2, "开放工具块不应折叠（需保持输出可见）"
