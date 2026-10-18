"""空模式提词：不再使用「一次性输出所有计划」的旧句式。"""

from __future__ import annotations

from tests._prompt_semantics import assert_contains, read_prompt


def test_legacy_one_shot_plan_phrasing_absent():
    text = read_prompt()
    for clause in ("输出所有计划", "强制按照计划执行", "强制读完所有相关的源码后"):
        assert clause not in text, f"旧计划句式未删除: {clause}"


def test_plan_rule_kept():
    assert_contains(read_prompt(), "先计划再执行", "按顺序编号", "不写入文件")
