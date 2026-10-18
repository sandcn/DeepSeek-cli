"""空模式提词：复杂任务先计划再执行。"""

from __future__ import annotations

from tests._prompt_semantics import assert_contains, read_prompt


def test_plan_before_action():
    text = read_prompt()
    assert_contains(
        text,
        "先计划再执行",
        "按顺序编号",
        "目标",
        "步骤",
        "涉及文件",
        "影响范围",
        "不写入文件",
    )


def test_simple_task_may_skip_explicit_plan():
    assert "简单任务可省略显式计划" in read_prompt()
