"""空模式提词：修改完成后用 bash_opt 实操一遍完整流程。"""

from __future__ import annotations

from tests._prompt_semantics import assert_contains, read_prompt


def test_fix_then_bash_opt_flow():
    text = read_prompt()
    assert_contains(
        text,
        "修改完成后用 `bash_opt` 实际操作一遍完整流程",
        "启动",
        "交互/运行",
        "查看结果",
        "不能只看静态代码或单元测试",
    )
