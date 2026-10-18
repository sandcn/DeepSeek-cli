"""空模式提词：存在未完成项时继续执行。"""

from __future__ import annotations

from tests._prompt_semantics import assert_contains, read_prompt


def test_continue_while_unfinished():
    text = read_prompt()
    assert_contains(
        text,
        "只要还有未完成项就继续执行",
        "不得提前收尾",
        "无法完成时明确说明阻塞原因和剩余事项",
        "禁止假装完成",
    )
