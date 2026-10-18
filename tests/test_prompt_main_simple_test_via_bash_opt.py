"""空模式提词：测试与完整流程验证使用 bash_opt。"""

from __future__ import annotations

from tests._prompt_semantics import assert_contains, read_prompt


def test_tests_and_flow_use_bash_opt():
    text = read_prompt()
    assert_contains(
        text,
        "`bash_opt`",
        "实际操作一遍完整流程",
        "并运行确认通过",
    )
