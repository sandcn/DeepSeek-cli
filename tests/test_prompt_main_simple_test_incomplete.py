"""空模式提词：测试缺失或失败不得视为完成。"""

from __future__ import annotations

from tests._prompt_semantics import assert_contains, read_prompt


def test_tests_required_and_not_weakened():
    text = read_prompt()
    assert_contains(
        text,
        "按项目既有测试目录与语言惯例新增或更新对应测试",
        "`tests/` 下新增或更新",
        "运行确认通过",
        "不得删除或弱化测试",
        "确需调整测试时",
        "交付说明写明理由",
    )
