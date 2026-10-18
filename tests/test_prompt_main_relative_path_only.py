"""空模式提词：工具路径参数使用相对路径。"""

from __future__ import annotations

from tests._prompt_semantics import assert_contains, read_prompt


def test_relative_paths_required():
    text = read_prompt()
    assert_contains(text, "路径参数使用相对路径", "相对当前工作目录")
