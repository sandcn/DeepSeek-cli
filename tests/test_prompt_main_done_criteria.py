"""空模式提词：完成的判定标准。"""

from __future__ import annotations

from tests._prompt_semantics import assert_contains, read_prompt


def test_done_criteria():
    text = read_prompt()
    assert_contains(
        text,
        "所有要求已落实",
        "测试通过",
        "实际运行无报错",
        "未引入回归",
        "没有验证结论",
        "视为未完成",
    )
