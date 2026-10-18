"""空模式提词：禁止不实现与降级实现的最高优先级红线。

用户要求：在 prompts_export_main_empty.md 中明确禁止不实现、降级实现等，
并用最严重的语气声明其为不可豁免的红线。
"""

from __future__ import annotations

from tests._prompt_semantics import assert_contains, read_prompt


def _rule_block() -> str:
    """返回「实现完整性红线」条目及其全部缩进子条目。"""
    lines = read_prompt().splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line.startswith("- **实现完整性红线")),
        None,
    )
    assert start is not None, "缺少「实现完整性红线」条目"
    block = [lines[start]]
    for line in lines[start + 1:]:
        if not line.startswith("  - "):
            break
        block.append(line)
    return "\n".join(block)


def test_redline_declares_highest_severity():
    """红线必须以最严重语气声明：零容忍、无豁免、一票否决、直接判失败。"""
    rule = _rule_block()
    assert_contains(
        rule,
        "最高优先级",
        "零容忍",
        "不可协商",
        "无豁免权",
        "一票否决",
        "绝无例外",
        "硬性红线",
        "直接判失败",
        "不容辩解",
        "不予受理",
    )


def test_forbids_not_implementing():
    """禁止不实现：任一功能/约束/分支未真实落地即整体未完成。"""
    rule = _rule_block()
    assert_contains(
        rule,
        "禁止不实现",
        "每一项功能",
        "每一条约束",
        "每一个分支",
        "真实实现",
        "漏做",
        "只做 happy path",
        "no-op",
        "未落实",
        "判定为未完成",
    )


def test_forbids_downgraded_implementation():
    """禁止降级实现：封杀一切简化版、占位与冒充形式。"""
    rule = _rule_block()
    assert_contains(
        rule,
        "禁止降级实现",
        "简化版",
        "阉割版",
        "低配替代",
        "占位符",
        "stub",
        "TODO",
        "假数据",
        "空实现",
        "mock",
        "硬编码返回值",
        "缩小需求范围",
        "一项都不许打折",
    )


def test_rejects_every_excuse():
    """任何理由都不成立，一律驳回，不得据此跳过或降级。"""
    rule = _rule_block()
    assert_contains(
        rule,
        "任何理由都不成立",
        "难",
        "复杂",
        "工作量大",
        "耗时",
        "时间紧",
        "上下文不足",
        "收益小",
        "风险高",
        "依赖缺失",
        "环境不支持",
        "一律驳回",
        "绝不能以阻塞为名宣称完成",
    )


def test_forbids_fake_completion():
    """禁止伪装完成：伪造输出、删弱测试、未运行测试均视为未完成。"""
    rule = _rule_block()
    assert_contains(
        rule,
        "禁止伪装完成",
        "伪造命令输出",
        "运行结果",
        "截图",
        "日志",
        "删除、弱化、跳过测试",
        "测试未实际运行或运行失败",
        "一律视为未完成",
    )


def test_requires_itemized_acceptance_before_delivery():
    """交付前必须逐条对照原始要求验收，存在缺项即禁止完成/交付。"""
    rule = _rule_block()
    assert_contains(
        rule,
        "交付前强制验收",
        "逐条对照用户原始要求",
        "验收结论",
        "不遗漏",
        "不简化",
        "不降级",
        "不得提前收尾",
        "禁止假装完成",
        "禁止声明完成",
        "禁止交付",
    )


def test_violation_fails_round_and_requires_redo():
    """违规即当轮失败，必须补全重做并如实披露，任何开脱均无效。"""
    rule = _rule_block()
    assert_contains(
        rule,
        "一律判定为失败",
        "补全为完整实现",
        "如实披露",
        "禁止隐瞒",
        "禁止辩解",
        "全部无效",
        "未明确解除本红线的指令都不构成豁免",
    )


def test_collaboration_section_has_same_level_redline():
    """协作与安全章节须并列声明与安全红线同级的一票否决。"""
    text = read_prompt()
    assert_contains(
        text,
        "实现完整性（一票否决，与安全红线同级）",
        "后果与触犯安全红线相同",
        "唯一权威口径",
    )


def test_delivery_and_done_criteria_require_full_implementation():
    """交付输出与完成标准都要把不实现/降级实现判为未完成。"""
    text = read_prompt()
    assert_contains(
        text,
        "交付前必须逐条对照用户原始要求给出验收结论",
        "存在未实现、降级实现或未验证项时禁止声明完成",
        "存在任何不实现、降级实现或未验证项的，一律视为未完成",
    )
