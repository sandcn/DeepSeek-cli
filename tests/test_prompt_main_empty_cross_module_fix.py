"""空模式提词：bash_opt 实操中发现的其它模块 bug 也必须强制修复。

用户要求：用 ``bash_opt`` 实操时，如果发现别的模块有 bug 和问题，
也要修复别的模块，语气要严重一点。

本测试固化「修改后 bash_opt 实操三查红线」中的跨模块修复条款：
无论问题是否本次改动引入、是否在任务范围内，都必须当场修到根因、
补测试并重新实操复核；以「不在本次范围」「不是我改的代码」「以后单独
处理」等理由放任不管，直接判定本轮任务失败。
"""

from __future__ import annotations

from tests._prompt_semantics import assert_contains, read_prompt


def _rule_block() -> str:
    """返回「修改后 bash_opt 实操三查红线」条目及其全部缩进子条目。"""
    lines = read_prompt().splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line.startswith("- **修改后 bash_opt 实操三查红线")),
        None,
    )
    assert start is not None, "缺少「修改后 bash_opt 实操三查红线」条目"
    block = [lines[start]]
    for line in lines[start + 1:]:
        if not line.startswith("  - "):
            break
        block.append(line)
    return "\n".join(block)


def test_cross_module_defect_must_be_fixed():
    """发现任何其它模块的问题都必须当场修复，不受本次改动范围限制。"""
    rule = _rule_block()
    assert_contains(
        rule,
        "跨模块缺陷必须按第零条处理",
        "任何其他模块",
        "上游调用方",
        "下游被调用方",
        "共享库",
        "公共组件",
        "无论是否本次改动引入",
        "无论是否在本次任务范围内",
        "立即当场修复",
        "补齐对应测试",
        "重新实操复核",
        "直到问题彻底消失",
    )


def test_cross_module_scope_excuses_are_rejected():
    """封杀「不在本次范围 / 不是我改的 / 以后单独处理」等推诿说辞。"""
    rule = _rule_block()
    assert_contains(
        rule,
        "不在本次改动范围",
        "不是我改的代码",
        "属于别的模块",
        "先记录、以后单独处理",
        "提个 issue 交出去",
        "告诉用户一声就行了",
        "一句都不成立",
        "未申请扩大授权、未如实报告、或擅自越权修复的",
        "当轮任务直接判定失败",
    )


def test_cross_module_fix_must_hit_root_cause():
    """必须追踪全量调用链修到根因，禁止只修症状或用补丁/吞异常掩盖。"""
    rule = _rule_block()
    assert_contains(
        rule,
        "`grep`",
        "`glob`",
        "全量调用链",
        "全部引用",
        "能修底层就不在上层打补丁",
        "修到根因",
        "禁止视而不见",
        "禁止跳过",
        "禁止只修本次改动涉及的部分",
        "禁止绕过/注释掉/临时屏蔽了事",
        "禁止把「已记录/已上报」当作处理完成",
        "禁止只修表面症状",
        "吞异常",
        "try-catch",
        "直到不再复现",
    )


def test_cross_module_rule_declares_highest_severity():
    """跨模块条款以最严重语气声明：最高优先级、零容忍、一票否决、绝无例外。"""
    rule = _rule_block()
    assert_contains(
        rule,
        "最高优先级",
        "零容忍",
        "一票否决",
        "谁都不许划界推诿",
    )


def test_other_sections_reference_cross_module_duty():
    """本节声明、协作安全、完成标准与最小改动条款都要覆盖跨模块修复责任。"""
    text = read_prompt()
    assert_contains(
        text,
        "实操中暴露的其他模块的 bug 与缺陷",
        "跨模块的问题既不按第零条申请也不如实报告",
        "实操三查缺失（一票否决，违反后果与安全红线相同，裁决层级次于安全红线）",
        "在实操中发现了其他模块的 bug 却以「不在本次改动范围」",
        "跨模块缺陷未按第零条申请扩大授权、未如实报告",
        "本条不是拒绝修别的模块的挡箭牌",
    )
