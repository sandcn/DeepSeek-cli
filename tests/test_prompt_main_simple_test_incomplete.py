"""空模式提词：测试缺失或失败不得视为完成。"""

from __future__ import annotations

from tests._prompt_semantics import assert_contains, read_prompt


def test_tests_required_and_not_weakened():
    text = read_prompt()
    assert_contains(
        text,
        "在 `tests/` 下新增或更新",
        "单元测试",
        "并运行确认通过",
        "不得删除或弱化测试",
        "测试确需调整时说明理由",
    )
