"""空模式提词：同类规则已合并，条目无重复。"""

from __future__ import annotations

from tests._prompt_semantics import assert_contains, prompt_bullets, read_prompt


def test_merged_sections_present():
    text = read_prompt()
    assert_contains(
        text,
        "## 语言与风格",
        "## 工作方式",
        "## 工具与验证",
        "## 协作与安全",
        "## 交付输出",
    )


def test_no_duplicate_bullets():
    bullets = prompt_bullets()
    assert len(bullets) == len(set(bullets))


def test_condensed_excuse_rules_keep_semantics():
    text = read_prompt()
    assert_contains(text, "不遗漏", "不简化", "不降级")
    assert_contains(text, "复杂", "耗时", "上下文不足", "收益小")
