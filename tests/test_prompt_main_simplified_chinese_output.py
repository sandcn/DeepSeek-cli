"""空模式提词：思考与回答使用简体中文。"""

from __future__ import annotations

from tests._prompt_semantics import assert_contains, read_prompt


def test_simplified_chinese_output():
    text = read_prompt()
    assert_contains(text, "一律使用简体中文", "代码、命令、标识符、路径保持原样")
