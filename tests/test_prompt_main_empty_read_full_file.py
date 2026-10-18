"""空模式提词：首次读取文件须完整读取。"""

from __future__ import annotations

from tests._prompt_semantics import assert_contains, read_prompt


def test_first_read_is_full():
    text = read_prompt()
    assert_contains(text, "首次读取文件时完整读取", "大文件或日志可分段")


def test_read_file_tool_is_required():
    assert_contains(read_prompt(), "读取用 `read_file`", "不要用 bash")
