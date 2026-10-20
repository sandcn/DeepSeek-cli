"""空模式提词：结构、元文件保护与交付输出。"""

from __future__ import annotations

from tests._prompt_semantics import assert_contains, read_prompt

HEADINGS = (
    "# 全局约束",
    "## 语言与风格",
    "## 工作方式",
    "## 工具与验证",
    "## 协作与安全",
    "## 交付输出",
)

META_FILES = (
    "global.md",
    "main.md",
    "plan.md",
    "think.md",
    "map.md",
    "review.md",
    "execute.md",
)


def test_structure_and_role():
    text = read_prompt()
    assert text.startswith("你是一位严谨、务实的软件工程师助手")
    for heading in HEADINGS:
        assert heading in text, f"缺少章节: {heading}"


def test_meta_files_protected():
    text = read_prompt()
    assert "元文件" in text
    for name in META_FILES:
        assert name in text, f"缺少元文件: {name}"


def test_completion_output_fields():
    assert_contains(
        read_prompt(),
        "修改范围",
        "改动内容与动机",
        "未做事项",
        "验证结论",
        "没有验证结论",
    )


def test_changelog_ban_kept():
    assert "CHANGELOG/变更日志" in read_prompt()
