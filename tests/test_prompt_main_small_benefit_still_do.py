"""空模式提词：收益小不得作为跳过理由。"""

from __future__ import annotations

from tests._prompt_semantics import assert_contains, read_prompt


def test_small_benefit_is_not_an_excuse():
    text = read_prompt()
    assert_contains(text, "收益小", "为由跳过")
