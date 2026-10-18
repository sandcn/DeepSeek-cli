"""空模式提词：bash 不得替代任何内部专用工具（最高优先级红线）。

用户需求：把「不能用 bash 替代内部工具」写成**最严重的语气**。对应提词
条目「bash 替代内部工具红线」，要求它：

- 与「验证时序红线」「bash 禁改文件红线」同级：最高优先级、一票否决；
- 点名全部内部工具及各自的 shell 替代命令黑名单；
- 「只读看看」「结果一样」等借口一律驳回；
- 触犯即当轮交付失败，必须改用内部工具重做。
"""

from __future__ import annotations

import pytest

from tests._prompt_semantics import assert_contains, read_prompt

#: 红线条目前缀（顶级 bullet）
_RULE_PREFIX = "- **bash 替代内部工具红线"

#: 必须被点名的内部专用工具
_TOOLS = (
    "`read`",
    "`grep`",
    "`glob`",
    "`ls`",
    "`write`",
    "`edit`",
    "`mkdir`",
    "`cp`",
    "`mv`",
    "`rm`",
    "`read_image`",
)

#: 每个内部工具对应的 shell 替代命令（必须出现在黑名单里）
_REPLACEMENTS = (
    ("`read`", "`cat`"),
    ("`grep`", "`rg`"),
    ("`glob`", "`find`"),
    ("`ls`", "`tree`"),
    ("`write`", "`tee`"),
    ("`mkdir`", "`mkdir -p`"),
    ("`rm`", "`find -delete`"),
    ("`read_image`", "`base64`"),
)


def rule_block() -> str:
    """返回「bash 替代内部工具红线」条目及其全部子项（缺失即失败）。"""
    lines = read_prompt().splitlines()
    start = next(
        (index for index, line in enumerate(lines) if line.startswith(_RULE_PREFIX)),
        None,
    )
    assert start is not None, f"提示词缺少条目: {_RULE_PREFIX}"
    block = [lines[start]]
    for line in lines[start + 1:]:
        if not line.startswith("  - "):
            break
        block.append(line)
    assert len(block) > 1, "红线条目下没有任何子项"
    return "\n".join(block)


def test_rule_is_highest_priority_red_line():
    """语气必须是最严重级别：最高优先级、一票否决、触犯即失败。"""
    assert_contains(
        rule_block(),
        "最高优先级",
        "零容忍",
        "一票否决",
        "绝无例外",
        "当轮任务直接判失败",
    )


@pytest.mark.parametrize("tool", _TOOLS)
def test_rule_names_every_internal_tool(tool: str):
    """每个内部专用工具都必须被点名。"""
    assert_contains(rule_block(), tool)


@pytest.mark.parametrize(("tool", "bash_command"), _REPLACEMENTS)
def test_rule_blacklists_shell_replacements(tool: str, bash_command: str):
    """每个工具的 shell 替代命令都必须列入黑名单。"""
    assert_contains(rule_block(), tool, bash_command)


def test_readonly_and_equivalence_excuses_are_rejected():
    """「只读看看」「结果一样」「工具不好用」等借口必须被明确驳回。"""
    assert_contains(
        rule_block(),
        "只是看看",
        "全部不成立",
        "全部驳回",
        "结果一样",
        "一律不构成理由",
    )


def test_only_exception_is_missing_internal_tool():
    """唯一例外：内部工具集根本没有对应工具，且需注明例外原因。"""
    assert_contains(
        rule_block(),
        "内部工具集中根本不存在对应工具",
        "bash 仅用于构建、安装依赖、运行测试、git、进程和系统信息查询",
        "例外原因",
    )


def test_violation_fails_and_must_be_redone_with_internal_tools():
    """违规即当轮失败，必须改用内部工具重做，禁止辩解。"""
    assert_contains(
        rule_block(),
        "一律判定为失败",
        "用内部工具重做",
        "禁止辩解",
        "下次注意",
    )


def test_coordination_section_restates_rule_as_veto():
    """「协作与安全」须以与安全红线同级的措辞重申该规则。"""
    bullets = [line for line in read_prompt().splitlines() if line.startswith("- ")]
    rule = next(
        (line for line in bullets if line.startswith("- **bash 替代内部工具（")),
        None,
    )
    assert rule is not None, "协作与安全缺少「bash 替代内部工具」条目"
    assert_contains(rule, "一票否决", "与安全红线同级", "一次都不行", "不构成豁免")
