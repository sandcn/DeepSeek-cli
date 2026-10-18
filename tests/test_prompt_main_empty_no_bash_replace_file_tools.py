"""空模式提词：不得用 bash 命令替代文件类专用工具。

覆盖 read/edit/write/mv/cp/glob/grep/mkdir/ls 九个工具：既要求条目把它们
列为必须使用的专用工具，也要求给出对应的 bash 禁用命令映射。
"""

from __future__ import annotations

import pytest

from tests._prompt_semantics import assert_contains, prompt_bullets

_FILE_TOOLS = (
    "`read`",
    "`edit`",
    "`write`",
    "`mv`",
    "`cp`",
    "`glob`",
    "`grep`",
    "`mkdir`",
    "`ls`",
)


def _bullet(prefix: str) -> str:
    """返回以 ``prefix`` 开头的提词条目行（缺失即失败）。"""
    for line in prompt_bullets():
        if line.startswith(prefix):
            return line
    raise AssertionError(f"提示词缺少条目: {prefix}")


def test_forbidden_bash_file_tool_rule_lists_all_tools():
    """「禁止用 bash 替代文件工具」条目必须点名全部九个专用工具。"""
    rule = _bullet("- **禁止用 bash 替代文件工具**")
    assert_contains(rule, *_FILE_TOOLS)
    assert_contains(rule, "禁止用 bash 兜底", "确需例外")


def test_bash_boundary_rule_covers_file_tools():
    """「bash 边界」条目同样要把九个工具列入不可替代范围。"""
    rule = _bullet("- **bash 边界**")
    assert_contains(rule, *_FILE_TOOLS)
    assert_contains(
        rule,
        "文件读取",
        "写入/修改",
        "搜索",
        "查找",
        "列目录",
        "复制",
        "移动",
        "建目录",
    )


@pytest.mark.parametrize(
    ("tool", "bash_command"),
    [
        ("`read`", "`cat`"),
        ("`write`", "`tee`"),
        ("`grep`", "`rg`"),
        ("`glob`", "`find`"),
        ("`ls`", "`dir`"),
        ("`mkdir`", "`touch`"),
    ],
)
def test_bash_replacement_command_is_forbidden(tool: str, bash_command: str):
    """每个工具都要有明确的 bash 替代命令黑名单。"""
    rule = _bullet("- **禁止用 bash 替代文件工具**")
    assert_contains(rule, tool, bash_command)
