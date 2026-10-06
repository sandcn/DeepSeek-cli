"""prompts_export_main_empty.md 新增「无项目信息时强制 find 获取全部目录」约束回归测试。

需求（2026-10-06）：在空模式提词中新增约束——没有任何项目信息时，
强制用 find 工具得到当前项目的所有目录，建立全貌认知后再分析或动手。
"""

from __future__ import annotations

from pathlib import Path

import pytest

MAIN_PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "prompts_export_main_empty.md"

RULE_KEY = "没有任何项目信息时强制用 find 工具得到当前项目的所有目录"
LEGACY_RULE_KEY = "分析项目前强制得到所有目录"


@pytest.fixture(scope="module")
def main_prompt_lines() -> list:
    assert MAIN_PROMPT.exists(), f"缺少文件: {MAIN_PROMPT}"
    return MAIN_PROMPT.read_text(encoding="utf-8").splitlines()


class TestEmptyPromptFindDirsWhenNoProjectInfo:

    def test_prompt_exists(self):
        """提示词文件存在。"""
        assert MAIN_PROMPT.exists()

    def test_new_rule_present(self, main_prompt_lines: list):
        """新增的「无项目信息时强制 find 获取全部目录」约束须存在。"""
        text = "\n".join(main_prompt_lines)
        assert RULE_KEY in text, f"缺少约束: {RULE_KEY}"
        rule = next(line for line in main_prompt_lines if RULE_KEY in line)
        assert "find" in rule
        assert "所有目录" in rule

    def test_rule_contains_enforcement(self, main_prompt_lines: list):
        """约束须包含强制性措辞与递归获取全貌的要求。"""
        rule = next(line for line in main_prompt_lines if RULE_KEY in line)
        assert "强制" in rule
        assert "递归" in rule or "完整目录结构" in rule

    def test_rule_placed_before_legacy_rule(self, main_prompt_lines: list):
        """新增约束须位于既有「分析项目前强制得到所有目录」约束之前（合并后同条目内亦须维持先后）。"""
        text = "\n".join(main_prompt_lines)
        assert text.index(RULE_KEY) < text.index(LEGACY_RULE_KEY)

    def test_other_constraints_untouched(self, main_prompt_lines: list):
        """其余全局约束不受影响，未被误删。"""
        text = "\n".join(main_prompt_lines)
        assert "你是一位乐于助人的软件工程师助手。" in text
        assert "# 全局约束" in text
        assert "思考跟回答强制简体中文输出" in text
        assert LEGACY_RULE_KEY in text
        assert "禁止采信代码的任何注释" in text

    def test_only_empty_version_changed(self):
        """本次仅修改 empty 版本，其它提示词文件不受影响。"""
        base = MAIN_PROMPT.parent
        for other in ("prompts_export_main.md", "prompts_export_main_simple.md"):
            content = (base / other).read_text(encoding="utf-8")
            assert RULE_KEY not in content, f"{other} 不应包含本次新增约束"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
