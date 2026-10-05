"""prompts_export_main_empty.md「强制禁止因为复杂就选择简单实现」规则回归测试。

需求（2026-10-05）：全局约束中新增规则，强制禁止因为复杂就选择简单实现，
必须强制实现所有内容，任何需求项都不得因复杂而简化、省略、删减、跳过。
"""

from __future__ import annotations

from pathlib import Path

import pytest

MAIN_PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "prompts_export_main_empty.md"


@pytest.fixture(scope="module")
def main_prompt_text() -> str:
    assert MAIN_PROMPT.exists(), f"缺少文件: {MAIN_PROMPT}"
    return MAIN_PROMPT.read_text(encoding="utf-8")


class TestMainNoSimplifyDueToComplexityRule:

    def test_prompt_exists(self):
        """提示词文件存在。"""
        assert MAIN_PROMPT.exists()

    def test_prompt_forbids_simplify_due_to_complexity(self, main_prompt_text: str):
        """提词须含「强制禁止因为复杂就选择简单实现」。"""
        assert "强制禁止因为复杂就选择简单实现" in main_prompt_text

    def test_prompt_marks_rule_as_red_line(self, main_prompt_text: str):
        """该规则须为「红线 · 一票否决」级别。"""
        assert "强制禁止因为复杂就选择简单实现（红线 · 一票否决）" in main_prompt_text

    def test_prompt_requires_implement_all(self, main_prompt_text: str):
        """须要求强制实现所有内容。"""
        assert "必须强制实现所有内容" in main_prompt_text

    def test_prompt_bans_complexity_excuses(self, main_prompt_text: str):
        """须点名禁止以复杂/难度大/工作量大为由选择简单实现。"""
        for word in ("复杂", "难度大", "工作量大"):
            assert word in main_prompt_text, f"应点名「{word}」"
        assert "选择简单实现或简化方案" in main_prompt_text

    def test_prompt_forbids_each_reduction(self, main_prompt_text: str):
        """须逐项禁止因复杂而简化、省略、删减、跳过、低配替代。"""
        for word in ("简化", "省略", "删减", "跳过", "低配方案替代"):
            assert word in main_prompt_text, f"应禁止因复杂而{word}"

    def test_prompt_keeps_downgrade_ban(self, main_prompt_text: str):
        """不得破坏原有「强势禁止降级实现代码」红线。"""
        assert "强势禁止降级实现代码（红线 · 一票否决）" in main_prompt_text

    def test_prompt_keeps_no_hard_task_skip(self, main_prompt_text: str):
        """不得破坏原有「强势禁止因为难就不做」。"""
        assert "强势禁止因为难就不做" in main_prompt_text


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
