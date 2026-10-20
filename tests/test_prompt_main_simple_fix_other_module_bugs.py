"""空模式提词：与本任务相关的缺陷一并修复并补测试。"""

from __future__ import annotations

from tests._prompt_semantics import assert_contains, read_prompt


def test_related_defects_are_fixed():
    text = read_prompt()
    assert_contains(
        text,
        "发现与本任务直接相关",
        "不修就无法完成用户明确要求",
        "一并修复并补测试",
        "不做无关重构",
    )
