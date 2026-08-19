"""prompts_export_review.md「根因修复」规则回归测试。

需求（2026-08-20）：上层与低层代码均能解决的问题，未修改低层代码 → 计 P0。
校验 review 审查提示词中已写入该强制规则（基本原则 + P0 分级 + 自审清单）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

REVIEW_PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "prompts_export_review.md"


@pytest.fixture(scope="module")
def review_prompt_text() -> str:
    assert REVIEW_PROMPT.exists(), f"缺少文件: {REVIEW_PROMPT}"
    return REVIEW_PROMPT.read_text(encoding="utf-8")


class TestReviewLowLevelRootFixRule:

    def test_basic_principle_requires_low_level_fix(self, review_prompt_text: str):
        """基本原则须含「根因修复（强制）」且指向低层（根因层）代码。"""
        assert "根因修复（强制）" in review_prompt_text
        assert "低层（根因层）代码" in review_prompt_text
        assert "仅在上层打补丁" in review_prompt_text

    def test_p0_grading_covers_low_level_not_modified(self, review_prompt_text: str):
        """P0 分级须含：上层与低层均能解决时未修改低层代码 → P0。"""
        assert "上层与低层代码均能解决问题时未修改低层代码" in review_prompt_text
        # 该条目必须位于 P0 定义行内
        p0_line = next(
            line for line in review_prompt_text.splitlines()
            if line.strip().startswith("- **P0 致命**")
        )
        assert "上层与低层代码均能解决问题时未修改低层代码" in p0_line

    def test_self_check_item7_arch_design_includes_root_fix(self, review_prompt_text: str):
        """自审检查清单第 7 项（架构与设计）须检查修复是否落在低层。"""
        assert "问题修复是否落在低层（根因层）而非仅在上层打补丁" in review_prompt_text
        assert "上层与低层均能解决时未修改低层 → P0" in review_prompt_text

    def test_principle_links_to_grading_section(self, review_prompt_text: str):
        """基本原则中的 P0 指引须能链接到「问题分级」章节。"""
        assert "见「问题分级」" in review_prompt_text
        assert "问题分级" in review_prompt_text


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
