"""prompts_export_main_empty.md「做任何事情前强制输出计划」规则回归测试。

现行空模式提词强制要求：做任何事情前先输出计划——明确本次目标、按顺序
编号的步骤清单（每步写清做什么、涉及哪些文件与影响范围），输出计划后
严格按计划逐步执行；禁止未输出计划就直接动手、禁止执行计划外的动作、
禁止跳过或擅自变更计划步骤；计划须建立在「先充分认知再分析、再动手」之上。
"""

from __future__ import annotations

from pathlib import Path

import pytest

MAIN_PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "prompts_export_main_empty.md"

RULE_KEY = "做任何事情前强制输出计划"
PREVIOUS_RULE_KEY = "先充分认知再分析、再动手，禁止未充分了解就动手"


@pytest.fixture(scope="module")
def main_prompt_text() -> str:
    assert MAIN_PROMPT.exists(), f"缺少文件: {MAIN_PROMPT}"
    return MAIN_PROMPT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def rule_line(main_prompt_text: str) -> str:
    matched = [ln for ln in main_prompt_text.splitlines() if ln.startswith("- ") and RULE_KEY in ln]
    assert matched, f"缺少约束: {RULE_KEY}"
    return matched[0]


class TestPlanBeforeActionRule:

    def test_prompt_exists(self):
        """提示词文件存在。"""
        assert MAIN_PROMPT.exists()

    def test_rule_present_on_single_line(self, rule_line: str):
        """提词须含该约束，且落在同一行，避免语义脱离。"""
        assert RULE_KEY in rule_line

    def test_rule_is_red_line(self, rule_line: str):
        """该规则须为「红线 · 一票否决」级别。"""
        assert "（红线 · 一票否决）" in rule_line

    def test_rule_requires_plan_output_before_action(self, rule_line: str):
        """须要求做任何事情前先输出计划。"""
        assert "先输出计划" in rule_line

    def test_rule_requires_ordered_steps(self, rule_line: str):
        """计划须含按顺序编号的步骤清单。"""
        assert "步骤清单" in rule_line
        assert "按顺序" in rule_line
        assert "编号" in rule_line

    def test_rule_requires_scope_info(self, rule_line: str):
        """计划须写明涉及文件与影响范围。"""
        assert "涉及哪些文件" in rule_line
        assert "影响范围" in rule_line

    def test_rule_requires_follow_plan(self, rule_line: str):
        """输出计划后须严格按计划逐步执行。"""
        assert "严格按计划逐步执行" in rule_line

    def test_rule_forbids_acting_without_plan(self, rule_line: str):
        """禁止未输出计划就直接动手。"""
        assert "禁止未输出计划就直接动手" in rule_line

    def test_rule_forbids_out_of_plan_actions(self, rule_line: str):
        """禁止执行计划外的动作。"""
        assert "禁止执行计划外的动作" in rule_line

    def test_rule_forbids_skipping_or_changing_steps(self, rule_line: str):
        """禁止跳过或擅自变更计划步骤，确需调整须先更新计划。"""
        assert "禁止跳过或擅自变更计划步骤" in rule_line
        assert "先更新计划再执行" in rule_line

    def test_rule_after_cognition_rule(self, main_prompt_text: str):
        """该规则须位于「先充分认知再分析、再动手」之后，逻辑连贯。"""
        assert main_prompt_text.index(PREVIOUS_RULE_KEY) < main_prompt_text.index(RULE_KEY)

    def test_previous_rule_kept(self, main_prompt_text: str):
        """先行认知约束未被误删。"""
        assert PREVIOUS_RULE_KEY in main_prompt_text
        assert "修改代码前强制跟踪所有相关代码" in main_prompt_text

    def test_no_duplicate_entry_lines(self, main_prompt_text: str):
        """新增条目不产生完全重复条目行。"""
        entries = [ln for ln in main_prompt_text.splitlines() if ln.startswith("- ")]
        assert len(entries) == len(set(entries)), "存在重复条目行"

    def test_legacy_plan_phrasing_not_reintroduced(self, main_prompt_text: str):
        """旧「一次性输出所有计划」句式不得随新规则恢复。"""
        assert "强制读完所有相关的源码后" not in main_prompt_text
        assert "输出所有计划" not in main_prompt_text
        assert "强制按照计划执行" not in main_prompt_text

    def test_other_constraints_untouched(self, main_prompt_text: str):
        """其余全局约束不受影响，未被误删。"""
        assert main_prompt_text.startswith("你是一位乐于助人的软件工程师助手。")
        assert "# 全局约束" in main_prompt_text
        assert "思考跟回答强制简体中文输出" in main_prompt_text
        assert "强制完整实现用户的所有要求" in main_prompt_text
        assert "plan execute agent 强制串行" in main_prompt_text

    def test_only_empty_version_changed(self):
        """本次仅修改 empty 版本，其它提示词文件不受影响。"""
        base = MAIN_PROMPT.parent
        for other in ("prompts_export_main.md", "prompts_export_main_simple.md"):
            content = (base / other).read_text(encoding="utf-8")
            assert RULE_KEY not in content, f"{other} 不应包含本次新增约束"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
