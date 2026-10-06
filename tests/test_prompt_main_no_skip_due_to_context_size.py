"""prompts_export_main_empty.md「强制禁止因为上下文大小就不执行」规则回归测试。

需求（2026-10-06）：全局约束中新增规则，强制禁止因为上下文大小就不执行，
上下文大小不得作为跳过读取、跳过执行、简化、降级或拒绝任何任务与需求项的借口。
"""

from __future__ import annotations

from pathlib import Path

import pytest

MAIN_PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "prompts_export_main_empty.md"


@pytest.fixture(scope="module")
def main_prompt_text() -> str:
    assert MAIN_PROMPT.exists(), f"缺少文件: {MAIN_PROMPT}"
    return MAIN_PROMPT.read_text(encoding="utf-8")


class TestMainNoSkipDueToContextSizeRule:

    def test_prompt_exists(self):
        """提示词文件存在。"""
        assert MAIN_PROMPT.exists()

    def test_prompt_forbids_skip_due_to_context_size(self, main_prompt_text: str):
        """提词须含「强制禁止因为上下文大小就不执行」。"""
        assert "强制禁止因为上下文大小就不执行" in main_prompt_text

    def test_prompt_marks_rule_as_red_line(self, main_prompt_text: str):
        """该规则须为「红线 · 一票否决」级别。"""
        assert "强制禁止因为上下文大小就不执行（红线 · 一票否决）" in main_prompt_text

    def test_prompt_bans_context_size_excuses(self, main_prompt_text: str):
        """须点名禁止以各种「上下文大小」类理由不做。"""
        for word in (
            "上下文不足",
            "上下文太大",
            "上下文窗口限制",
            "token 超限",
            "超过上下文长度",
            "上下文快满了",
            "上下文溢出",
            "上下文放不下",
        ):
            assert word in main_prompt_text, f"应点名禁止以「{word}」为由不做"

    def test_prompt_forbids_each_reduction(self, main_prompt_text: str):
        """须逐项禁止因上下文大小而跳过、省略、删减、简化、降级、拆分、拖延、跳过读取、拒绝。"""
        for word in ("跳过", "省略", "删减", "简化", "降级", "拆分", "拖延", "跳过读取", "拒绝"):
            assert word in main_prompt_text, f"应禁止因上下文大小而{word}"

    def test_prompt_requires_complete_execution(self, main_prompt_text: str):
        """须要求完整读取所需内容并完整执行到位。"""
        assert "必须完整读取所需内容并完整执行到位" in main_prompt_text

    def test_prompt_bans_context_size_as_excuse(self, main_prompt_text: str):
        """须声明上下文大小不得作为不做的任何借口。"""
        assert "上下文大小不得作为不做的任何借口" in main_prompt_text

    def test_prompt_keeps_neighbor_small_benefit_rule(self, main_prompt_text: str):
        """不得破坏相邻原有约束（因收益小就不做的禁令）。"""
        assert "强制禁止因收益小就不做（红线 · 一票否决）" in main_prompt_text


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
