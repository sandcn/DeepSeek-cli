"""空元素同行多行文本渲染回归测试。

2026-10 review 修复：``_append_void_tail`` 原先只取 ``inline_lines`` 第一行，
``<input> 第一行<br>第二行`` 的后续行被静默丢弃。
"""

from __future__ import annotations

from src.renderer.ansi._html_block import html_block_line, open_html_block


def test_input_void_tail_keeps_all_lines():
    rows = html_block_line('<input type="checkbox"> 第一行\n第二行', "")
    assert len(rows) == 2
    assert "第一行" in rows[0].plain
    assert "第二行" in rows[1].plain


def test_img_void_tail_keeps_all_lines():
    rows = html_block_line('<img src="a.png" alt="图"> 说明一\n说明二', "")
    assert len(rows) == 2
    assert "说明一" in rows[0].plain
    assert "说明二" in rows[1].plain


def test_open_html_block_void_tail_keeps_all_lines():
    rows, _state = open_html_block(
        "input", {"type": "checkbox"}, tail="完成一\n完成二",
    )
    assert len(rows) == 2
    assert "完成一" in rows[0].plain
    assert "完成二" in rows[1].plain


def test_void_tail_single_line_unchanged():
    rows = html_block_line('<input type="checkbox"> 完成', "")
    assert len(rows) == 1
    assert "完成" in rows[0].plain
