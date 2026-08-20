"""prompts_export_review.md「已读源码全量 Bug 强制列出」规则回归测试。

需求（2026-08-20）：已经读取的所有源码的 Bug 都要列出来，强制全量、强制所有。
校验 review 审查提示词中已写入该强制规则（基本原则-行为优先级）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

REVIEW_PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "prompts_export_review.md"


@pytest.fixture(scope="module")
def review_prompt_text() -> str:
    assert REVIEW_PROMPT.exists(), f"缺少文件: {REVIEW_PROMPT}"
    return REVIEW_PROMPT.read_text(encoding="utf-8")


class TestReviewFullBugListRule:

    def test_rule_text_present(self, review_prompt_text: str):
        """提词须含「已读源码全量 Bug 强制列出（强制）」规则标题。"""
        assert "已读源码全量 Bug 强制列出（强制）" in review_prompt_text

    def test_rule_covers_user_requirement_words(self, review_prompt_text: str):
        """规则须完整覆盖需求原文关键词：已经读取的所有源码、强制全量、强制所有。"""
        rule_line = next(
            line for line in review_prompt_text.splitlines()
            if "已读源码全量 Bug 强制列出" in line
        )
        assert "已经读取的所有源码" in rule_line
        assert "强制全量" in rule_line
        assert "强制所有" in rule_line

    def test_rule_located_in_basic_principles(self, review_prompt_text: str):
        """规则须位于「基本原则」章节的「行为优先级」列表内。"""
        principles_start = review_prompt_text.index("## 基本原则")
        workflow_start = review_prompt_text.index("## 审查工作流")
        section = review_prompt_text[principles_start:workflow_start]
        assert "已读源码全量 Bug 强制列出（强制）" in section
        assert "行为优先级" in section

    def test_rule_forbids_omission(self, review_prompt_text: str):
        """规则须强调禁止遗漏、禁止选择性输出。"""
        rule_line = next(
            line for line in review_prompt_text.splitlines()
            if "已读源码全量 Bug 强制列出" in line
        )
        assert "禁止遗漏" in rule_line
        assert "禁止选择性输出" in rule_line


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
