"""prompts_export_main_empty.md「收益小的东西也强制做」规则回归测试。

需求（2026-10-05）：全局约束中新增规则，强制禁止因收益小就不做，
收益小不得作为跳过、省略、简化、降级或拒绝任何需求项的借口，
收益小的东西也强制做。
"""

from __future__ import annotations

from pathlib import Path

import pytest

MAIN_PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "prompts_export_main_empty.md"


@pytest.fixture(scope="module")
def main_prompt_text() -> str:
    assert MAIN_PROMPT.exists(), f"缺少文件: {MAIN_PROMPT}"
    return MAIN_PROMPT.read_text(encoding="utf-8")


class TestMainSmallBenefitStillDoRule:

    def test_prompt_exists(self):
        """提示词文件存在。"""
        assert MAIN_PROMPT.exists()

    def test_prompt_forbids_skip_due_to_small_benefit(self, main_prompt_text: str):
        """提词须含「强制禁止因收益小就不做」。"""
        assert "强制禁止因收益小就不做" in main_prompt_text

    def test_prompt_marks_rule_as_red_line(self, main_prompt_text: str):
        """该规则须为「红线 · 一票否决」级别。"""
        line = next(
            (ln for ln in main_prompt_text.splitlines() if "强制禁止因收益小就不做" in ln),
            "",
        )
        assert "（红线 · 一票否决）" in line

    def test_prompt_requires_small_benefit_still_do(self, main_prompt_text: str):
        """须显式要求「收益小的东西也强制做」。"""
        assert "收益小的东西也强制做" in main_prompt_text

    def test_prompt_bans_small_benefit_excuses(self, main_prompt_text: str):
        """须点名禁止以各种「收益小」类理由不做。"""
        for word in ("收益小", "价值低", "回报低", "性价比低", "不值得", "作用不大"):
            assert word in main_prompt_text, f"应点名禁止以「{word}」为由不做"

    def test_prompt_forbids_each_reduction(self, main_prompt_text: str):
        """须逐项禁止因收益小而跳过、省略、删减、简化、降级、拖延、选择性实现、拒绝。"""
        for word in ("跳过", "省略", "删减", "简化", "降级", "拖延", "选择性实现", "拒绝"):
            assert word in main_prompt_text, f"应禁止因收益小而{word}"

    def test_prompt_requires_complete_implementation(self, main_prompt_text: str):
        """须要求无论收益多小都要完整实现并交付。"""
        assert "完整实现并交付" in main_prompt_text
        assert "一律必须完整实现" in main_prompt_text

    def test_prompt_bans_benefit_as_excuse(self, main_prompt_text: str):
        """须声明收益小不得作为不做的任何借口。"""
        assert "收益小不得作为不做的任何借口" in main_prompt_text

    def test_prompt_keeps_neighbor_complexity_rules(self, main_prompt_text: str):
        """不得破坏相邻原有约束（时间/宏大复杂度不做的禁令）。"""
        assert "禁止用时间去评任务的复杂度就选择不做" in main_prompt_text
        assert "禁止用宏大去评任务的复杂度就选择不做" in main_prompt_text


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
