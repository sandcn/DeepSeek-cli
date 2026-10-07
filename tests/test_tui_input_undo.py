"""输入缓冲撤销/重做单元测试（2026-10-07 操作优化：Ctrl+Z / Ctrl+Y）。

覆盖：
  - 字符输入合并为一个撤销单元（窗口内多次输入一次撤销）；
  - 粘贴 / 退格 / kill / set_buffer / 历史浏览各自的撤销点；
  - 重做（redo）与「新编辑清空重做栈」语义；
  - 提交（Enter）清空撤销/重做栈；
  - 撤销栈上限。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.tui._input_buffer import _UNDO_LIMIT, InputBufferEditor


@pytest.fixture
def editor() -> InputBufferEditor:
    return InputBufferEditor(Path("/tmp/_undo_test_history"))


def test_char_edits_coalesce_into_one_undo(editor):
    editor.handle_char("a")
    editor.handle_char("b")
    assert editor.get_current_text() == "ab"
    editor._undo()
    assert editor.get_current_text() == ""


def test_paste_undo(editor):
    editor.handle_chars("hello")
    editor._undo()
    assert editor.get_current_text() == ""


def test_backspace_undo(editor):
    editor.handle_chars("abc")
    editor._backspace()
    assert editor.get_current_text() == "ab"
    editor._undo()
    assert editor.get_current_text() == "abc"


def test_kill_to_eol_undo(editor):
    editor.handle_chars("abcdef")
    for _ in range(3):
        editor._left()
    editor._kill_to_eol()
    assert editor.get_current_text() == "abc"
    editor._undo()
    assert editor.get_current_text() == "abcdef"


def test_set_buffer_undo(editor):
    editor.handle_chars("first")
    editor.set_buffer("second")
    assert editor.get_current_text() == "second"
    editor._undo()
    assert editor.get_current_text() == "first"


def test_history_navigation_undo(editor):
    editor._history = ["prev"]
    editor.handle_chars("cur")
    editor._up()
    assert editor.get_current_text() == "prev"
    editor._undo()
    assert editor.get_current_text() == "cur"


def test_redo_restores(editor):
    editor.handle_chars("hello")
    editor._undo()
    assert editor.get_current_text() == ""
    editor._redo()
    assert editor.get_current_text() == "hello"


def test_new_edit_clears_redo(editor):
    editor.handle_chars("hello")
    editor._undo()
    editor.handle_char("x")
    assert editor.get_current_text() == "x"
    editor._redo()  # 重做栈已被新编辑清空
    assert editor.get_current_text() == "x"


def test_submit_clears_undo_stack(editor):
    editor.handle_chars("abc")
    editor._enter()
    assert editor.get_current_text() == ""
    assert editor._undo_stack == []
    editor._undo()  # no-op
    assert editor.get_current_text() == ""


def test_reset_clears_undo_stack(editor):
    editor.handle_chars("abc")
    editor.reset()
    assert editor._undo_stack == []
    editor._undo()
    assert editor.get_current_text() == ""


def test_undo_stack_limit(editor):
    for i in range(_UNDO_LIMIT + 50):
        editor.set_buffer(str(i))
    assert len(editor._undo_stack) <= _UNDO_LIMIT


def test_undo_without_history_is_noop(editor):
    editor._undo()
    assert editor.get_current_text() == ""
    editor._redo()
    assert editor.get_current_text() == ""
