"""空模式提词：上下文不足不得作为跳过理由。"""

from __future__ import annotations

from tests._prompt_semantics import assert_contains, read_prompt


def test_context_size_is_not_an_excuse():
    text = read_prompt()
    assert_contains(text, "上下文不足", "为由跳过", "不得提前收尾")
