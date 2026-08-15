"""验证 prompts_export_main_empty.md 的调度规则：分析项目前强制得到所有目录。

本次文档修改要求：
- 分析项目前必须先 find 递归获取项目完整目录结构，此步骤不可跳过。
本测试对文档关键规则文本做存在性与一致性断言。
"""

from pathlib import Path

PROMPTS_MAIN_EMPTY = (
    Path(__file__).resolve().parents[2] / "prompts" / "prompts_export_main_empty.md"
)

# 本次修改必须落地的关键规则文本片段
REQUIRED_RULES = [
    "分析项目前强制得到所有目录",
    "先 find 递归获取项目完整目录结构",
    "此步骤不可跳过",
]


def _doc_text() -> str:
    assert PROMPTS_MAIN_EMPTY.is_file(), f"文档不存在: {PROMPTS_MAIN_EMPTY}"
    return PROMPTS_MAIN_EMPTY.read_text(encoding="utf-8")


def test_dir_before_analysis_rule_present():
    """分析项目前强制得到所有目录的规则必须全部存在。"""
    text = _doc_text()
    missing = [rule for rule in REQUIRED_RULES if rule not in text]
    assert not missing, f"文档缺少以下关键规则文本: {missing}"


def test_rule_placed_in_global_constraints():
    """新规则必须位于全局约束列表内。"""
    text = _doc_text()
    section_start = text.index("# 全局约束")
    rules_end = text.index("# 元文件保护") if "# 元文件保护" in text else len(text)
    section = text[section_start:rules_end]
    assert "分析项目前强制得到所有目录" in section, "规则不在全局约束章节内"
