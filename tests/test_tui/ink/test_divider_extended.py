"""Divider 控件扩展测试（全面控件化方案B，2026-08-16）。

控件扩展（供 StatusBar/InputArea 分隔线委托）：
  - trailing 右侧内容（StyledRun 列表或 Line——左侧填充 + 右侧内容，
    行宽恒 = width，与 _theme.sep_line 构建语义对齐）
  - 纯填充分隔线（无 title/trailing）宽度与样式
"""

from __future__ import annotations

from src.tui.core.style import Style
from src.tui.ink.element import h
from src.tui.ink.output import Line, StyledRun
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.components import render_frame
from src.tui.ink.widgets.display import Divider
from src.tui._width import wcswidth_simple


def _render(element, width: int = 80, height: int = 24):
    rec = Reconciler()
    root = rec.create_root()
    rec.render(root, element, width, height)
    frame = render_frame(root, width)
    return rec, root, frame


def _frame_plain(frame) -> list[str]:
    return [ln.plain for ln in frame.lines]


def test_divider_plain_fill():
    """纯填充分隔线：width 行宽、字符重复、样式生效。"""
    rec, root, frame = _render(h(Divider, {
        "width": 20, "char": "\u2501", "style": Style(fg=45),
    }))
    lines = _frame_plain(frame)
    assert lines == ["\u2501" * 20]
    # 样式（fg=45）应用到行 run
    run = frame.lines[0].runs[0]
    assert run.style is not None and run.style.fg == 45


def test_divider_trailing_runs():
    """trailing 右侧内容：左侧填充 + 右侧内容，行宽恒 = width。"""
    trailing = [StyledRun(" CPU:45%", Style(fg=214))]
    rec, root, frame = _render(h(Divider, {
        "width": 30, "char": "\u2501", "trailing": trailing,
        "style": Style(fg=238),
    }))
    lines = _frame_plain(frame)
    joined = "".join(lines)
    assert joined.endswith(" CPU:45%"), f"右侧内容应保留: {lines}"
    assert joined.count("\u2501") == 30 - len(" CPU:45%")
    assert len(lines) == 1
    assert wcswidth_simple(joined) == 30


def test_divider_trailing_line_object():
    """trailing 为 ink Line 对象（Line.runs 提取）。"""
    line = Line.of(" 12:00:00", Style(fg=110))
    rec, root, frame = _render(h(Divider, {
        "width": 30, "char": "\u2501", "trailing": line,
    }))
    joined = "".join(_frame_plain(frame))
    assert joined.endswith(" 12:00:00")
    assert wcswidth_simple(joined) == 30


def test_divider_trailing_overflow_truncated():
    """trailing 超宽时截断至 width（行宽不变量保持）。"""
    trailing = [StyledRun("x" * 60, Style(fg=214))]
    rec, root, frame = _render(h(Divider, {
        "width": 30, "char": "\u2501", "trailing": trailing,
    }))
    joined = "".join(_frame_plain(frame))
    assert wcswidth_simple(joined) == 30
    assert "x" * 30 in joined or joined.startswith("x")


def test_divider_title_still_works():
    """title 分隔线行为保持（回归）。"""
    rec, root, frame = _render(h(Divider, {"title": "测试", "width": 20}))
    lines = _frame_plain(frame)
    assert len(lines) == 1
    assert "测试" in lines[0]
    assert wcswidth_simple(lines[0]) == 20
