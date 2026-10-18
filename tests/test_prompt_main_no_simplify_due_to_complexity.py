"""空模式提词：不得以复杂为由简化或降级。"""

from __future__ import annotations

from tests._prompt_semantics import assert_contains, read_prompt


def test_complexity_is_not_an_excuse():
    text = read_prompt()
    assert_contains(
        text,
        "不以复杂",
        "为由跳过",
        "不简化",
        "不降级",
        "占位符",
        "stub",
        "TODO",
        "假数据",
    )
