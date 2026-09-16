"""prompts_export_main_empty.md「只能 review 代码一次」规则回归测试。

需求（2026-09-17）：全局约束中的 review 规则由「修复后循环 review
直到 P0~P3 清零」改为「只能 review 代码一次」，禁止重复派发
review agent、禁止多轮复审或循环 review。
"""

from __future__ import annotations

from pathlib import Path

import pytest

MAIN_PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "prompts_export_main_empty.md"


@pytest.fixture(scope="module")
def main_prompt_text() -> str:
    assert MAIN_PROMPT.exists(), f"缺少文件: {MAIN_PROMPT}"
    return MAIN_PROMPT.read_text(encoding="utf-8")


class TestMainReviewOnceRule:

    def test_prompt_exists(self):
        """提示词文件存在。"""
        assert MAIN_PROMPT.exists()

    def test_prompt_requires_review_once(self, main_prompt_text: str):
        """提词须含「只能 review 代码一次」。"""
        assert "只能 review 代码一次" in main_prompt_text

    def test_prompt_still_requires_one_review_agent(self, main_prompt_text: str):
        """仍须强制派发一个 review agent 进行代码审查。"""
        assert "必须强制派发一个 review agent 进行代码审查" in main_prompt_text

    def test_prompt_bans_repeat_dispatch(self, main_prompt_text: str):
        """须禁止重复派发 review agent。"""
        assert "禁止重复派发 review agent" in main_prompt_text

    def test_prompt_bans_multi_round_review(self, main_prompt_text: str):
        """须禁止多轮复审或循环 review。"""
        assert "禁止多轮复审或循环 review" in main_prompt_text

    def test_prompt_processes_result_once(self, main_prompt_text: str):
        """审查结果只处理一次。"""
        assert "审查结果只处理一次" in main_prompt_text

    def test_prompt_drops_loop_until_clear(self, main_prompt_text: str):
        """不得再保留「逐条修复，直到全部清零」的循环 review 表述。"""
        assert "直到全部清零" not in main_prompt_text

    def test_prompt_keeps_review_prompt_constraint(self, main_prompt_text: str):
        """review agent 提词仍只能含要 review 的文件列表。"""
        assert "review agent 的提词强制只能包含要 review 的文件列表" in main_prompt_text
        assert "严禁附加任何其它内容" in main_prompt_text


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
