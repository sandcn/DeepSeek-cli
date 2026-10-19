"""空模式提词：修复代码问题可不限次数尝试。

用户要求：在 prompts_export_main_empty.md 中写入「修复代码问题，可以无限次尝试」，
即修复代码缺陷时不设尝试次数上限，但同时必须基于失败根因调整方案、
不得机械重试、不得虚报成功，并且文件内不得再留有与本规则冲突的次数配额。
"""

from __future__ import annotations

import re

from tests._prompt_semantics import assert_contains, read_prompt

_RULE_PREFIX = "- **修复代码问题可无限次尝试**"
_DELIVERY_TOKEN = "修复代码问题可以无限次尝试"


def _section(title: str) -> str:
    """返回标题为 ``title`` 的章节正文（到下一个同级或更高级标题为止）。"""
    lines = read_prompt().splitlines()
    pattern = re.compile(rf"^(#{{1,4}})\s+{re.escape(title)}\s*$")
    for index, line in enumerate(lines):
        matched = pattern.match(line)
        if not matched:
            continue
        level = len(matched.group(1))
        body: list[str] = []
        for follow in lines[index + 1:]:
            heading = re.match(r"^(#{1,4})\s+", follow)
            if heading and len(heading.group(1)) <= level:
                break
            body.append(follow)
        return "\n".join(body)
    raise AssertionError(f"缺少章节: {title}")


def test_rule_placed_in_working_style_section():
    """无限次尝试属于工作方式，须位于「工具与验证」章节之前。"""
    text = read_prompt()
    rule_pos = text.index(_RULE_PREFIX)
    verification_pos = text.index("## 工具与验证")
    assert 0 < rule_pos < verification_pos, "无限次尝试规则应位于「工作方式」章节内"
    assert _RULE_PREFIX in _section("全局约束"), "无限次尝试规则应归属「全局约束」章节"


def test_unlimited_attempts_explicitly_stated():
    """规则必须明确「不设尝试次数上限」「直至问题真正解决」。"""
    text = read_prompt()
    assert_contains(
        text,
        _RULE_PREFIX,
        "修复代码缺陷",
        "不设尝试次数上限",
        "反复修改",
        "反复验证",
        "直至问题真正解决",
    )


def test_attempts_must_follow_root_cause_not_mechanical_retry():
    """无限次尝试仍须基于失败根因，禁止用相同方式机械重试。"""
    text = read_prompt()
    assert_contains(
        text,
        "基于失败根因调整方案",
        "禁止用完全相同的方式机械重试",
    )


def test_unresolved_requires_honest_report():
    """确实无法解决时必须如实说明卡点与已尝试方案，不得虚报成功。"""
    text = read_prompt()
    assert_contains(
        text,
        "如实说明卡点",
        "已尝试的全部方案与证据",
        "不得隐瞒或虚报成功",
    )


def test_delivery_section_drops_failure_cap_wording():
    """交付输出章节须同步为无限次尝试，不再保留「连续失败时如实报告」的配额口径。"""
    delivery = _section("交付输出")
    assert_contains(
        delivery,
        _DELIVERY_TOKEN,
        "不设次数上限",
        "直到缺陷被真正修复并验证通过",
        "如实报告卡点与已尝试方案",
    )
    assert "连续失败时如实报告" not in delivery, (
        "交付输出仍保留与无限次尝试冲突的「连续失败时如实报告」口径"
    )


def test_no_conflicting_attempt_quota_left_in_prompt():
    """全文件不得残留与本规则冲突的重试次数配额（如「最多重试 N 次」「连续 N 次失败停止」）。"""
    text = read_prompt()
    # 允许「不设尝试次数上限」这类否定表述，仅排查真正的配额断言。
    quota_patterns = (
        r"最多重试\s*\d+\s*次",
        r"重试\s*\d+\s*次",
        r"连续\s*\d+\s*次\s*失败\s*停止",
    )
    hits = [m.group(0) for p in quota_patterns for m in re.finditer(p, text)]
    assert not hits, f"提示词仍存在重试次数配额: {hits}"
