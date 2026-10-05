"""prompts_export_main_empty.md「思考跟回答强制简体中文输出」规则回归测试。

需求（2026-10-05）：将空模式提词中的「推理跟回答纯中文输出」改为
「思考跟回答强制简体中文输出，禁止其他语言」，强调简体中文强制输出。
"""

from __future__ import annotations

from pathlib import Path

import pytest

MAIN_PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "prompts_export_main_empty.md"

RULE_LINE = "思考跟回答强制简体中文输出"
BAN_PHRASE = "禁止其他语言"


@pytest.fixture(scope="module")
def main_prompt_text() -> str:
    assert MAIN_PROMPT.exists(), f"缺少文件: {MAIN_PROMPT}"
    return MAIN_PROMPT.read_text(encoding="utf-8")


class TestSimplifiedChineseOutputRule:

    def test_prompt_exists(self):
        """提示词文件存在。"""
        assert MAIN_PROMPT.exists()

    def test_rule_present(self, main_prompt_text: str):
        """提词须含「思考跟回答强制简体中文输出」。"""
        assert RULE_LINE in main_prompt_text

    def test_old_wording_removed(self, main_prompt_text: str):
        """旧表述「推理跟回答纯中文输出」须已移除。"""
        assert "推理跟回答纯中文输出" not in main_prompt_text

    def test_requires_simplified_chinese(self, main_prompt_text: str):
        """须点名强制简体中文。"""
        assert "强制简体中文输出" in main_prompt_text
        assert "简体中文" in main_prompt_text

    def test_covers_thinking_and_answer(self, main_prompt_text: str):
        """须覆盖思考与回答两部分。"""
        line = next((ln for ln in main_prompt_text.splitlines() if RULE_LINE in ln), "")
        assert line, "应存在该规则行"
        assert "思考" in line
        assert "回答" in line
        assert "推理" in line

    def test_bans_other_languages(self, main_prompt_text: str):
        """须禁止繁体中文、英文及任何其他语言。"""
        assert "禁止使用繁体中文、英文及任何其他语言" in main_prompt_text

    def test_rule_line_states_ban_other_languages(self, main_prompt_text: str):
        """规则行须直接点名「禁止其他语言」。"""
        line = next((ln for ln in main_prompt_text.splitlines() if RULE_LINE in ln), "")
        assert line, "应存在该规则行"
        assert BAN_PHRASE in line

    def test_rule_is_single_line(self, main_prompt_text: str):
        """规则须为单条全局约束项。"""
        lines = [ln for ln in main_prompt_text.splitlines() if RULE_LINE in ln]
        assert len(lines) == 1
        assert lines[0].startswith("- ")

    def test_other_constraints_kept(self, main_prompt_text: str):
        """其余全局约束不受影响。"""
        assert "强势禁止因为难就不做" in main_prompt_text
        assert "禁止采信代码的任何注释" in main_prompt_text
        assert "plan execute agent 强制串行" in main_prompt_text


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
