"""空模式提词：完整落实用户要求。"""

from __future__ import annotations

from tests._prompt_semantics import assert_contains, read_prompt


def test_all_user_specs_must_be_completed():
    text = read_prompt()
    assert_contains(text, "逐条落实用户要求", "不遗漏", "不简化", "不降级")


def test_unfinished_items_block_completion():
    assert_contains(read_prompt(), "未完成项", "不得提前收尾", "禁止假装完成")
