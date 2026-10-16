"""prompts_export_main_empty.md「强制完成用户指定的所有东西」规则回归测试。

需求（2026-10-17）：全局约束中新增规则，强制完成用户指定的所有东西——
用户指定的每一项都必须全部完成，禁止只完成其中一部分，禁止漏做、少做、
挑着做、挑容易的做、只做看起来重要的或把剩下的留着不做；用户明确指定的一切
（需求、指令、要求、修改、优化、修复、分析、读取、验证、文件、功能、任务等）
都必须逐项落地、一项不少，凡有任一项未完成一律视为未完成，禁止标记任务完成、
禁止交付，必须继续做完为止；完成后还须逐条对照用户原话核对。
"""

from __future__ import annotations

import pytest

from src.prompt_builder import builder
from tests._paths import prompts_dir, repo_root

_PROJECT_ROOT = repo_root()
MAIN_PROMPT = prompts_dir() / "prompts_export_main_empty.md"

RULE_TITLE = "强制完成用户指定的所有东西"
PRIOR_RULE_KEY = "强制完整实现用户的所有要求"
SPEC_KINDS = (
    "需求",
    "指令",
    "要求",
    "修改",
    "优化",
    "修复",
    "分析",
    "读取",
    "验证",
    "文件",
    "功能",
    "任务",
)


@pytest.fixture(scope="module")
def main_prompt_text() -> str:
    assert MAIN_PROMPT.exists(), f"缺少文件: {MAIN_PROMPT}"
    return MAIN_PROMPT.read_text(encoding="utf-8")


def _rule_lines(text: str) -> list:
    return [ln for ln in text.splitlines() if ln.startswith("- ") and RULE_TITLE in ln]


@pytest.fixture(scope="module")
def rule_line(main_prompt_text: str) -> str:
    matched = _rule_lines(main_prompt_text)
    assert matched, f"缺少约束: {RULE_TITLE}"
    return matched[0]


class TestMainCompleteAllUserSpecsRule:

    def test_prompt_exists(self):
        """提示词文件存在。"""
        assert MAIN_PROMPT.exists()

    def test_rule_present(self, rule_line: str):
        """提词须含「强制完成用户指定的所有东西」约束。"""
        assert RULE_TITLE in rule_line
        assert "用户指定的所有东西" in rule_line

    def test_rule_is_red_line(self, rule_line: str):
        """该规则须为「红线 · 一票否决」级别。"""
        assert "（红线 · 一票否决）" in rule_line

    def test_rule_is_single_entry(self, main_prompt_text: str):
        """该规则不得拆成多条重复条目。"""
        assert len(_rule_lines(main_prompt_text)) == 1, "该规则条目应唯一"

    def test_rule_requires_every_item_done(self, rule_line: str):
        """须要求用户指定的每一项都必须全部完成。"""
        assert "用户指定的每一项都必须全部完成" in rule_line
        assert "用户指定的所有东西都必须强制完成" in rule_line

    def test_rule_forbids_partial_completion(self, rule_line: str):
        """须禁止只完成其中一部分。"""
        assert "禁止只完成其中一部分" in rule_line

    def test_rule_forbids_missing_and_picking(self, rule_line: str):
        """须点名禁止漏做、少做、挑着做、挑容易的做、留着不做。"""
        for token in ("漏做", "少做", "挑着做", "挑容易的做", "把剩下的留着不做"):
            assert token in rule_line, f"应禁止「{token}」"

    def test_rule_forbids_only_doing_seemingly_important(self, rule_line: str):
        """须禁止只做看起来重要的部分。"""
        assert "只做看起来重要的" in rule_line

    def test_rule_enumerates_spec_kinds(self, rule_line: str):
        """须枚举「用户明确指定的一切」涵盖的内容种类。"""
        assert "用户明确指定的一切" in rule_line
        for kind in SPEC_KINDS:
            assert kind in rule_line, f"应枚举指定内容种类「{kind}」"

    def test_rule_requires_item_by_item_landing(self, rule_line: str):
        """须要求逐项落地、一项不少。"""
        assert "逐项落地" in rule_line
        assert "一项不少" in rule_line

    def test_rule_marks_incomplete(self, rule_line: str):
        """任一项未完成须一律视为未完成并禁止标记完成/交付。"""
        assert "一律视为未完成" in rule_line
        assert "禁止标记任务完成" in rule_line
        assert "禁止交付" in rule_line

    def test_rule_requires_continue_until_done(self, rule_line: str):
        """须要求必须继续做完为止。"""
        assert "必须继续做完为止" in rule_line

    def test_rule_requires_verbatim_check(self, rule_line: str):
        """完成后须逐条对照用户原话核对全部完成。"""
        assert "逐条对照用户原话核对" in rule_line
        assert "确认用户指定的所有东西全部完成" in rule_line
        assert "核对未通过必须继续做完" in rule_line

    def test_rule_after_prior_completeness_rule(self, main_prompt_text: str):
        """该规则须紧随「强制完整实现用户的所有要求」之后，逻辑连贯。"""
        assert main_prompt_text.index(PRIOR_RULE_KEY) < main_prompt_text.index(RULE_TITLE)

    def test_rule_inside_maintainability_section(self, main_prompt_text: str):
        """新规则落在「四、可维护性与完整实现」章节内。"""
        section_start = main_prompt_text.index("## 四、可维护性与完整实现")
        section_end = main_prompt_text.index("## 五、认知、探索与规划纪律")
        assert section_start < main_prompt_text.index(RULE_TITLE) < section_end

    def test_prior_rule_kept(self, main_prompt_text: str):
        """原「强制完整实现用户的所有要求」条目未被误删。"""
        assert PRIOR_RULE_KEY in main_prompt_text
        assert "验收未通过必须继续完善" in main_prompt_text

    def test_no_duplicate_entries(self, main_prompt_text: str):
        """新增条目后条目行仍无完全重复。"""
        entries = [ln for ln in main_prompt_text.splitlines() if ln.startswith("- ")]
        assert len(entries) == len(set(entries)), "存在重复条目行"

    def test_other_constraints_untouched(self, main_prompt_text: str):
        """其余全局约束不受影响，未被误删。"""
        assert main_prompt_text.startswith("你是一位乐于助人的软件工程师助手。")
        assert "# 全局约束" in main_prompt_text
        assert "思考跟回答强制简体中文输出" in main_prompt_text
        assert "强制禁止一切降级实现与非完整交付（红线 · 一票否决）" in main_prompt_text
        assert "强制所有修改完成后才能检查与交付（红线 · 一票否决）" in main_prompt_text

    def test_only_empty_version_changed(self):
        """本次仅修改 empty 版本，其它提示词文件不受影响。"""
        base = MAIN_PROMPT.parent
        for other in ("prompts_export_main.md", "prompts_export_main_simple.md"):
            content = (base / other).read_text(encoding="utf-8")
            assert RULE_TITLE not in content, f"{other} 不应包含本次修改约束"

    def test_loaded_prompt_contains_rule(self, monkeypatch):
        """空模式下实际加载的首段提词包含该规则。"""
        monkeypatch.setattr(builder, "build_work_md", lambda *a, **k: "")
        monkeypatch.setattr(builder, "build_environment_info", lambda *a, **k: "")
        monkeypatch.setattr(builder, "build_skills_prompt_section", lambda *a, **k: "")
        monkeypatch.setattr(builder, "_build_mcp_section", lambda *a, **k: "")
        original = builder.get_mode()
        try:
            builder.set_mode("empty")
            parts = builder.build_system_prompt(cwd=str(_PROJECT_ROOT))
            assert parts
            assert RULE_TITLE in parts[0]
        finally:
            builder.set_mode(original)


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
