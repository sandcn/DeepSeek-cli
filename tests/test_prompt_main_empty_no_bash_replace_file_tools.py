"""空模式提词：bash 边界不得替代文件类专用工具。

覆盖 read/edit/write/mv/cp/glob/grep/mkdir/ls 九个工具：既要求条目把它们
列为必须使用的专用工具，也要求给出对应的 shell 禁用命令映射。

原「禁止用 bash 替代文件工具」与「bash 边界」两个条目内容重复，已合并为
单一条目「bash 边界（禁止替代文件工具）」；本测试同步合并，覆盖范围不变。
"""

from __future__ import annotations

import pytest

from tests._prompt_semantics import assert_contains, prompt_bullets

#: 合并后的唯一条目名前缀
_BASH_RULE = "- **bash 边界"

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


def test_bash_boundary_lists_all_file_tools():
    """「bash 边界」条目必须点名全部九个专用工具及各类禁用命令。"""
    rule = _bullet(_BASH_RULE)
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
        "禁止用 bash 兜底",
        "确需例外",
        "运行与调试",
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
    """每个工具都要有明确的 shell 替代命令黑名单。"""
    rule = _bullet(_BASH_RULE)
    assert_contains(rule, tool, bash_command)
