"""用户消息样式（2026-10-22 用户需求）回归测试。

覆盖：
  - ``build_user_line`` 使用白色粗体（fg=255, bold=True）；
  - ``_user_marker_styled_lines`` 以 tool_card 背景色整行满宽填充；
  - 多行 / 长文本 / 空消息 / 终端 resize 下的行宽与背景不变量。
"""

from __future__ import annotations

import os

from src.tui.app._model_helpers import _user_marker_styled_lines
from src.tui.app._state_types import ChatBlock
from src.tui.app.apply import build_user_line
from src.tui.app.toolcard import card_bg_style


def _make_user_block(text: str) -> ChatBlock:
    return ChatBlock("user", build_user_line(text))


def _assert_user_row(row, width: int, bg: int) -> None:
    assert row.width == width
    assert row.runs[0].text.startswith("> ")
    assert row.runs[0].style.fg == 255
    assert row.runs[0].style.bold is True
    assert row.runs[0].style.bg == bg
    # 整行（含行尾填充空格）均为 tool_card 背景
    assert all(r.style is not None and r.style.bg == bg for r in row.runs)


def test_build_user_line_is_white_bold():
    lines = build_user_line("hello\n世界")
    assert len(lines) == 2
    for line in lines:
        assert line.runs
        for run in line.runs:
            assert run.style.fg == 255
            assert run.style.bold is True


def test_user_lines_full_width_card_bg():
    bg = card_bg_style().bg
    block = _make_user_block("hello\nworld")
    rows = _user_marker_styled_lines(block, 0, None, 40)

    assert len(rows) == 2
    for row in rows:
        _assert_user_row(row, 40, bg)
        # 短行行尾必须有背景色填充（整行占满终端宽度）
        assert row.runs[-1].text.strip() == ""


def test_user_lines_follow_resize():
    bg = card_bg_style().bg
    block = _make_user_block("a long message that should wrap on narrow terminals")
    rows_20 = _user_marker_styled_lines(block, 0, None, 20)
    rows_80 = _user_marker_styled_lines(block, 0, None, 80)

    assert rows_20 and rows_80
    for row in rows_20:
        _assert_user_row(row, 20, bg)
    for row in rows_80:
        _assert_user_row(row, 80, bg)


def test_user_lines_empty_message_keeps_full_width_bg():
    bg = card_bg_style().bg
    block = _make_user_block("")
    rows = _user_marker_styled_lines(block, 0, None, 30)

    assert len(rows) == 1
    _assert_user_row(rows[0], 30, bg)


def test_user_lines_long_text_never_exceeds_width():
    bg = card_bg_style().bg
    block = _make_user_block("x" * 200)
    rows = _user_marker_styled_lines(block, 0, None, 32)

    assert rows
    for row in rows:
        _assert_user_row(row, 32, bg)


def test_input_submit_text_invokes_direct_callback():
    """命令面板 Enter 路径（Input.submit_text）同样触发命令回显回调。"""
    from src.tui._input import Input

    read_fd, write_fd = os.pipe()
    try:
        inp = Input(fd=read_fd, history_file=None)
        seen = []
        inp.set_direct_submit_callback(lambda text: seen.append(text) or False)

        assert inp.submit_text("/help", append_history=False) is True
        assert seen == ["/help"]
        # 回调返回 False → 正常进入排队输入（由主循环执行命令）
        assert inp.has_queued_input() is True
        assert inp.get_queued_input() == "/help"
    finally:
        os.close(read_fd)
        os.close(write_fd)
