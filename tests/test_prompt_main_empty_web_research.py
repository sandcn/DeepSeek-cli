"""空模式提词：合理联网查证与来源标注。"""

from __future__ import annotations

from tests._prompt_semantics import assert_contains, read_prompt


def test_web_research_rule_present():
    """工作方式须要求「合理时机上网查证」，并给出触发场景与边界。"""
    text = read_prompt()
    assert_contains(
        text,
        "合理联网查证",
        "第三方库/框架",
        "官方文档",
        "`web_search`",
        "`web_fetch`",
        "版本与日期",
        "待验证",
        "以源码为准",
    )


def test_web_tool_boundary_documented():
    """工具与验证须说明联网工具用法与限制。"""
    text = read_prompt()
    assert_contains(
        text,
        "**联网**",
        "http/https",
        "不能替代读源码",
        "本机运行验证",
    )


def test_no_fabricated_external_facts():
    """表达与完成标准须禁止把未查证的外部信息写成事实。"""
    text = read_prompt()
    assert_contains(
        text,
        "不编造未查证的外部资料",
        "查证来源支撑",
    )
