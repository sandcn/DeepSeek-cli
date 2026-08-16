"""验证 prompts_export_main_empty.md 的调度规则：
分析项目前强制得到所有目录 + 强制用内部工具实现所有（禁用 bash 替换）。

本次文档修改要求：
- 分析项目前必须先 find 递归获取项目完整目录结构，此步骤不可跳过。
- 强制用内部工具实现所有功能，禁止用 bash 命令替换/替代内部工具。
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

# 强制用内部工具实现所有（禁用 bash 替换）的关键规则文本片段
REQUIRED_INTERNAL_TOOL_RULES = [
    "强制用内部工具实现所有",
    "禁止用 bash 命令替换/替代内部工具",
    "cat",
    "grep",
    "sed",
    "awk",
    "echo",
    "bash 仅限内部工具无法覆盖的场景",
    "需注明例外原因",
]


def _doc_text() -> str:
    assert PROMPTS_MAIN_EMPTY.is_file(), f"文档不存在: {PROMPTS_MAIN_EMPTY}"
    return PROMPTS_MAIN_EMPTY.read_text(encoding="utf-8")


def _global_constraints_section(text: str) -> str:
    section_start = text.index("# 全局约束")
    rules_end = text.index("# 元文件保护") if "# 元文件保护" in text else len(text)
    return text[section_start:rules_end]


def test_dir_before_analysis_rule_present():
    """分析项目前强制得到所有目录的规则必须全部存在。"""
    text = _doc_text()
    missing = [rule for rule in REQUIRED_RULES if rule not in text]
    assert not missing, f"文档缺少以下关键规则文本: {missing}"


def test_rule_placed_in_global_constraints():
    """新规则必须位于全局约束列表内。"""
    section = _global_constraints_section(_doc_text())
    assert "分析项目前强制得到所有目录" in section, "规则不在全局约束章节内"


def test_internal_tool_rule_present():
    """强制用内部工具实现所有（禁用 bash 替换）的关键规则必须全部存在。"""
    text = _doc_text()
    missing = [rule for rule in REQUIRED_INTERNAL_TOOL_RULES if rule not in text]
    assert not missing, f"文档缺少以下关键规则文本: {missing}"


def test_internal_tool_rule_placed_in_global_constraints():
    """强制用内部工具实现所有的规则必须位于全局约束列表内。"""
    section = _global_constraints_section(_doc_text())
    assert "强制用内部工具实现所有" in section, "规则不在全局约束章节内"


def test_internal_tool_rule_lists_positive_mappings():
    """规则必须列出内部工具的正向映射（读/写/搜/找/目录/复制/移动/删除）。"""
    section = _global_constraints_section(_doc_text())
    for tool in ["read_file", "write_file", "update_file", "search", "find", "ls", "mkdir", "cp", "mv", "rm"]:
        assert tool in section, f"规则缺少内部工具正向映射: {tool}"


def test_internal_tool_rule_forbids_bash_replacements():
    """规则必须明确禁止用 bash 命令替代内部工具（cat/grep/sed/awk/echo/find/ls）。"""
    section = _global_constraints_section(_doc_text())
    assert "禁止用 bash 命令替换/替代内部工具" in section
    forbid_start = section.index("禁止 `cat` 读文件")
    forbid_end = section.index("列目录等")
    forbid_list = section[forbid_start:forbid_end]
    for cmd in ["cat", "grep", "sed", "awk", "echo", "find", "ls"]:
        assert f"`{cmd}`" in forbid_list, f"规则未明确禁止 bash 命令替代: {cmd}"
