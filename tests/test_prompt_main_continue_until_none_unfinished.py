"""prompts_export_main_empty.md「有未完成的东西就继续调用工具完成，直到没有」规则回归测试。

需求（2026-10-17）：全局约束中新增规则——只要还有未完成的东西，
就必须强制继续调用工具把它做完，直到没有未完成的东西为止；
禁止在存在任何未完成项时停止调用工具、停止行动或结束回合，
禁止以「先暂停」「本轮到此为止」「下一轮再继续」「稍后补上」等理由
搁置未完成项；每完成一项必须立刻复查，只要还有未完成项就继续调用工具完成。
"""

from __future__ import annotations

import pytest

from src.prompt_builder import builder
from tests._paths import prompts_dir, repo_root

_PROJECT_ROOT = repo_root()
MAIN_PROMPT = prompts_dir() / "prompts_export_main_empty.md"

RULE_TITLE = "强制有未完成的东西就继续调用工具完成，直到没有"
PRIOR_RULE_KEY = "强制完成用户指定的所有东西"
UNFINISHED_KINDS = (
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
SHELVE_TOKENS = ("先暂停", "本轮到此为止", "下一轮再继续", "稍后补上")


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


class TestMainContinueUntilNoUnfinishedRule:

    def test_prompt_exists(self):
        """提示词文件存在。"""
        assert MAIN_PROMPT.exists()

    def test_rule_present(self, rule_line: str):
        """提词须含「有未完成的东西就继续调用工具完成」约束。"""
        assert RULE_TITLE in rule_line
        assert "继续调用工具" in rule_line
        assert "直到没有" in rule_line

    def test_rule_is_red_line(self, rule_line: str):
        """该规则须为「红线 · 一票否决」级别。"""
        assert "（红线 · 一票否决）" in rule_line

    def test_rule_is_single_entry(self, main_prompt_text: str):
        """该规则不得拆成多条重复条目。"""
        assert len(_rule_lines(main_prompt_text)) == 1, "该规则条目应唯一"

    def test_rule_requires_continue_while_unfinished(self, rule_line: str):
        """须要求「只要还有未完成的东西」就继续调用工具做完。"""
        assert "只要还有未完成的东西" in rule_line
        assert "就必须强制继续调用工具把它做完" in rule_line

    def test_rule_requires_until_none_left(self, rule_line: str):
        """须要求做到「直到没有未完成的东西为止」。"""
        assert "直到没有未完成的东西为止" in rule_line

    def test_rule_enumerates_unfinished_kinds(self, rule_line: str):
        """须枚举「未完成的东西」涵盖的内容种类。"""
        assert "未完成的东西" in rule_line
        for kind in UNFINISHED_KINDS:
            assert kind in rule_line, f"应枚举未完成项种类「{kind}」"

    def test_rule_forbids_stop_when_unfinished(self, rule_line: str):
        """禁止在存在任何未完成项时停止调用工具/停止行动/结束回合。"""
        assert "禁止在存在任何未完成项时停止调用工具" in rule_line
        assert "停止行动" in rule_line
        assert "结束回合" in rule_line

    def test_rule_forbids_shelving(self, rule_line: str):
        """须点名禁止以各种理由把未完成的东西搁置不做。"""
        assert "把未完成的东西搁置不做" in rule_line
        for token in SHELVE_TOKENS:
            assert token in rule_line, f"应禁止以「{token}」为理由搁置"

    def test_rule_requires_recheck_loop(self, rule_line: str):
        """每完成一项须立刻复查，直到确认没有任何未完成项为止。"""
        assert "必须立刻复查还有没有未完成的东西" in rule_line
        assert "直到复查确认没有任何未完成项为止" in rule_line

    def test_rule_after_prior_completeness_rule(self, main_prompt_text: str):
        """该规则须紧随「强制完成用户指定的所有东西」之后，逻辑连贯。"""
        assert main_prompt_text.index(PRIOR_RULE_KEY) < main_prompt_text.index(RULE_TITLE)

    def test_rule_inside_maintainability_section(self, main_prompt_text: str):
        """新规则落在「四、可维护性与完整实现」章节内。"""
        section_start = main_prompt_text.index("## 四、可维护性与完整实现")
        section_end = main_prompt_text.index("## 五、认知、探索与规划纪律")
        assert section_start < main_prompt_text.index(RULE_TITLE) < section_end

    def test_prior_rule_kept(self, main_prompt_text: str):
        """原「强制完成用户指定的所有东西」条目未被误删。"""
        assert PRIOR_RULE_KEY in main_prompt_text
        assert "核对未通过必须继续做完" in main_prompt_text

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
