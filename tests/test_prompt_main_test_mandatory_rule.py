"""prompts_export_main_empty.md「测试强制」「review agent 提词限定」规则回归测试。

需求（2026-09-11 用户需求）：
1. 修改代码后一定要增加对应的测试，测试文件统一放在 `tests` 目录下；
   禁止只改实现不补测试，未同步新增/更新对应测试的修改禁止标记完成。
2. 调用 review agent 时，其系统提词只允许包含所有修改文件的文件列表，
   强制禁止包含任何其他信息（任务描述、需求说明、背景上下文、约束条件、
   审查要求、补充指令等），系统提词有且仅有文件列表本身。
"""

from __future__ import annotations

from pathlib import Path

import pytest

MAIN_PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "prompts_export_main_empty.md"

TEST_MANDATORY_RULE = "**测试强制（红线 · 零豁免）**"
REVIEW_PROMPT_SCOPE_RULE = "**只允许包含所有修改文件的文件列表**"


@pytest.fixture(scope="module")
def main_prompt_text() -> str:
    assert MAIN_PROMPT.exists(), f"缺少文件: {MAIN_PROMPT}"
    return MAIN_PROMPT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def test_mandatory_line(main_prompt_text: str) -> str:
    assert TEST_MANDATORY_RULE in main_prompt_text, f"缺少规则: {TEST_MANDATORY_RULE}"
    return next(
        line for line in main_prompt_text.splitlines()
        if "测试强制" in line
    )


@pytest.fixture(scope="module")
def review_prompt_line(main_prompt_text: str) -> str:
    assert REVIEW_PROMPT_SCOPE_RULE in main_prompt_text, f"缺少规则: {REVIEW_PROMPT_SCOPE_RULE}"
    return next(
        line for line in main_prompt_text.splitlines()
        if "review agent" in line
    )


class TestTestMandatoryRule:

    def test_prompt_exists(self):
        """提示词文件存在。"""
        assert MAIN_PROMPT.exists()

    def test_rule_present(self, main_prompt_text: str):
        """提词须含「测试强制」规则条目。"""
        assert "测试强制" in main_prompt_text

    def test_rule_marks_red_line_zero_exemption(self, main_prompt_text: str):
        """该规则须为「红线 · 零豁免」级别。"""
        assert TEST_MANDATORY_RULE in main_prompt_text

    def test_rule_requires_new_or_updated_tests(self, test_mandatory_line: str):
        """规则须要求修改代码后增加对应测试。"""
        assert "修改代码后" in test_mandatory_line
        assert "增加对应的测试" in test_mandatory_line

    def test_rule_pins_tests_directory(self, test_mandatory_line: str):
        """规则须要求测试文件统一放在 tests 目录下。"""
        assert "`tests` 目录" in test_mandatory_line

    def test_rule_forbids_implementation_only(self, test_mandatory_line: str):
        """规则须禁止只改实现不补测试。"""
        assert "禁止只改实现不补测试" in test_mandatory_line

    def test_rule_forbids_marking_done_without_tests(self, test_mandatory_line: str):
        """规则须禁止在未同步新增/更新测试时标记完成。"""
        assert "未同步新增/更新对应测试的修改禁止标记完成" in test_mandatory_line


class TestReviewAgentPromptScopeRule:

    def test_rule_present(self, main_prompt_text: str):
        """提词须含 review agent 复查规则。"""
        assert "强制用 1 个 review agent 复查全部代码" in main_prompt_text

    def test_scope_limited_to_file_list(self, main_prompt_text: str):
        """review agent 的系统提词只允许包含所有修改文件的文件列表。"""
        assert REVIEW_PROMPT_SCOPE_RULE in main_prompt_text

    def test_scope_forbids_other_information(self, main_prompt_text: str):
        """须强制禁止包含文件列表以外的任何其他信息。"""
        assert "强制禁止包含任何其他信息" in main_prompt_text

    def test_scope_enumerates_forbidden_items(self, review_prompt_line: str):
        """须列举被禁止的典型信息类型。"""
        for item in ("任务描述", "需求说明", "背景上下文", "约束条件", "审查要求", "补充指令"):
            assert item in review_prompt_line, f"应列举: {item}"

    def test_scope_is_file_list_only(self, main_prompt_text: str):
        """系统提词有且仅有文件列表本身。"""
        assert "系统提词有且仅有文件列表本身" in main_prompt_text


class TestMainPromptTestMandatoryBuildIntegration:

    def test_builder_loads_rules(self):
        """builder 空模式加载的提词包含「测试强制」与「review agent 提词限定」两条规则。"""
        from src.prompt_builder import builder

        origin = builder.is_empty_mode()
        builder.set_empty_mode(True)
        try:
            prompts = builder.build_system_prompt(cwd=".")
        finally:
            builder.set_empty_mode(origin)
        assert prompts, "系统提示词不应为空"
        assert any("测试强制（红线 · 零豁免）" in p for p in prompts)
        assert any("只允许包含所有修改文件的文件列表" in p for p in prompts)


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
