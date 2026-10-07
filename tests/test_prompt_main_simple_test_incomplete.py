"""prompts_export_main_simple.md「测试没到位不算完成」规则回归测试。

需求（2026-10-08）：在简单模式提词的测试相关约束后，明确完成判定的兜底
红线——测试没到位一律不算完成：所有修改/新增功能必须补齐对应单元测试，
测试必须全部实际运行并通过；存在测试缺失、未覆盖本次修改、未运行或运行
失败，一律视为未完成，禁止标记任务完成、禁止交付。
"""

from __future__ import annotations

from pathlib import Path

import pytest

SIMPLE_PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "prompts_export_main_simple.md"

RULE_KEY = "强制测试没到位不算完成"
UNIT_TEST_RULE_KEY = "每次修改文件后，必须增加对应的单元测试"


@pytest.fixture(scope="module")
def simple_prompt_text() -> str:
    assert SIMPLE_PROMPT.exists(), f"缺少文件: {SIMPLE_PROMPT}"
    return SIMPLE_PROMPT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def rule_line(simple_prompt_text: str) -> str:
    matched = [ln for ln in simple_prompt_text.splitlines() if ln.startswith("- ") and RULE_KEY in ln]
    assert matched, f"缺少约束: {RULE_KEY}"
    return matched[0]


class TestSimplePromptTestIncompleteRule:

    def test_prompt_exists(self):
        """提示词文件存在。"""
        assert SIMPLE_PROMPT.exists()

    def test_rule_present(self, rule_line: str):
        """提词须含「测试没到位不算完成」约束。"""
        assert RULE_KEY in rule_line
        assert "测试没到位一律不算完成" in rule_line

    def test_rule_is_red_line(self, rule_line: str):
        """该规则须为「红线 · 一票否决」级别。"""
        assert "（红线 · 一票否决）" in rule_line

    def test_requires_unit_tests_added(self, rule_line: str):
        """须要求所有修改/新增功能补齐对应单元测试。"""
        assert "所有修改/新增功能" in rule_line
        assert "必须补齐对应单元测试" in rule_line

    def test_requires_tests_run_and_pass(self, rule_line: str):
        """须要求测试全部实际运行并通过。"""
        assert "测试必须全部实际运行并通过" in rule_line

    def test_lists_incomplete_conditions(self, rule_line: str):
        """须逐项点名测试缺失/未覆盖/未运行/失败四种未完成情形。"""
        for token in ("测试缺失", "测试未覆盖本次修改", "测试未运行", "测试运行失败"):
            assert token in rule_line, f"应点名「{token}」"

    def test_marks_incomplete_and_forbids_delivery(self, rule_line: str):
        """任一情形须一律视为未完成并禁止标记完成/交付。"""
        assert "一律视为未完成" in rule_line
        assert "禁止标记任务完成" in rule_line
        assert "禁止交付" in rule_line

    def test_rule_follows_unit_test_rule(self, simple_prompt_text: str):
        """该规则须紧随单元测试条目之后，逻辑连贯。"""
        assert simple_prompt_text.index(UNIT_TEST_RULE_KEY) < simple_prompt_text.index(RULE_KEY)

    def test_unit_test_rule_kept(self, simple_prompt_text: str):
        """原单元测试条目未被误删。"""
        assert UNIT_TEST_RULE_KEY in simple_prompt_text

    def test_other_constraints_untouched(self, simple_prompt_text: str):
        """其余全局约束不受影响，未被误删。"""
        assert simple_prompt_text.startswith("你是一位乐于助人的软件工程师助手。")
        assert "# 全局约束" in simple_prompt_text
        assert "推理跟回答纯中文输出" in simple_prompt_text
        assert "强制完整实现用户的所有要求" in simple_prompt_text
        assert "禁止采信代码的任何注释" in simple_prompt_text

    def test_only_simple_version_changed(self):
        """本次仅修改 simple 版本，其它提词文件不受影响。"""
        base = SIMPLE_PROMPT.parent
        for other in ("prompts_export_main_empty.md", "prompts_export_main.md"):
            content = (base / other).read_text(encoding="utf-8")
            assert RULE_KEY not in content, f"{other} 不应包含本次修改约束"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
