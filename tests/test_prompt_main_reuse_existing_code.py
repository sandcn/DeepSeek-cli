"""空模式提词：优先复用现有代码与约定。"""

from __future__ import annotations

from tests._prompt_semantics import assert_contains, read_prompt


def test_reuse_existing_code():
    text = read_prompt()
    assert_contains(
        text,
        "优先复用现有代码",
        "接口和约定",
        "不做无关重构",
        "发现与本任务直接相关",
        "一并修复并补测试",
    )
