"""空模式提词：提交与推送须经用户确认。"""

from __future__ import annotations

from tests._prompt_semantics import assert_contains, read_prompt


def test_commit_and_push_require_confirmation():
    text = read_prompt()
    assert_contains(
        text,
        "未经用户明确同意",
        "commit",
        "push",
        "user_select",
    )
