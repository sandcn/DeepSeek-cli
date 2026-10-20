"""空模式提词：用户没有明确要求改代码时，禁止自行修改。

用户要求：在 prompts_export_main_empty.md 中写明「用户没有明确要求修改代码，
就不要自己修改」，并用最严重的语气声明其为最高优先级、不可豁免的禁令。
"""

from __future__ import annotations

from tests._prompt_semantics import assert_contains, read_prompt


def _rule_block() -> str:
    """返回「第零条禁令」整节文本（到下一个二级标题为止）。"""
    lines = read_prompt().splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line.startswith("## 第零条禁令")),
        None,
    )
    assert start is not None, "缺少「第零条禁令」章节"
    block = [lines[start]]
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        block.append(line)
    return "\n".join(block)


def test_rule_is_the_first_section():
    """禁令必须排在最前，先于「语言与风格」等其余章节生效。"""
    text = read_prompt()
    rule_at = text.find("## 第零条禁令")
    style_at = text.find("## 语言与风格")
    assert rule_at != -1, "缺少「第零条禁令」章节"
    assert style_at != -1, "缺少「语言与风格」章节"
    assert rule_at < style_at, "「第零条禁令」必须排在「语言与风格」之前"


def test_declares_highest_severity():
    """以最严重语气声明：最高优先级、零容忍、一票否决、绝无例外。"""
    rule = _rule_block()
    assert_contains(
        rule,
        "最高优先级",
        "零容忍",
        "不可协商",
        "无豁免权",
        "一票否决",
        "绝无例外",
        "直接判定失败",
        "一律驳回",
        "不予受理",
    )


def test_forbids_unsolicited_modification():
    """没有明确要求时，一个字符都不许改，包括一切写操作工具与 shell 手段。"""
    rule = _rule_block()
    assert_contains(
        rule,
        "用户没有明确要求你修改代码",
        "绝对不许修改代码",
        "一个字符都不许",
        "`write`",
        "`edit`",
        "`cp`",
        "`mv`",
        "`rm`",
        "`mkdir`",
        "任何 shell / 脚本手段",
        "一次都不行",
        "一行都不行",
    )


def test_only_read_analyze_answer_without_authorization():
    """无授权时只允许读、分析、回答，不许「顺手把代码改了」。"""
    rule = _rule_block()
    assert_contains(
        rule,
        "只允许做什么",
        "`read`",
        "`grep`",
        "分析",
        "给建议",
        "不许多走一步",
        "顺便把代码改了",
    )


def test_defines_explicit_authorization():
    """明确授权必须同时具备明确动作、明确对象与不越界三条。"""
    rule = _rule_block()
    assert_contains(
        rule,
        "什么才算「明确授权」",
        "明确的**动作**",
        "明确的**对象**",
        "不得擅自扩大",
        "说改 A 就不许顺手改 B、C、D",
    )


def test_lists_cases_that_are_not_authorization():
    """提问、诊断、审查、要计划、讨论、含糊、自我说服全部不算授权。"""
    rule = _rule_block()
    assert_contains(
        rule,
        "以下全部不算授权",
        "提问类",
        "诊断类",
        "审查 / 评估类",
        "计划 / 文档类",
        "讨论 / 咨询类",
        "含糊类",
        "自我说服类",
        "「反正要改」",
        "「改了更好」",
        "「顺手就改了」",
        "全部无效、全部驳回",
    )


def test_ambiguous_intent_must_be_asked():
    """有歧义时默认「没有授权」，必须先确认，禁止自我解释成授权。"""
    rule = _rule_block()
    assert_contains(
        rule,
        "有歧义必须问，不许猜",
        "默认结论是「没有授权」",
        "`user_select`",
        "自我解释成授权",
    )


def test_self_check_before_any_write_tool():
    """调用任何写操作工具前必须自检是否获得授权，否则立即停手。"""
    rule = _rule_block()
    assert_contains(
        rule,
        "自检义务",
        "让我改代码或文件",
        "立即停手",
        "回到只读、只答的状态",
        "答不上来还动手，就是违规",
    )


def test_violation_fails_round_and_requires_rollback():
    """违规即当轮失败，必须回滚到原文并如实披露，任何开脱均无效。"""
    rule = _rule_block()
    assert_contains(
        rule,
        "一律判定为失败",
        "回滚",
        "恢复到改动前的原文",
        "如实披露",
        "禁止隐瞒",
        "禁止辩解",
        "全部无效",
    )


def test_not_usable_as_excuse_for_incomplete_work():
    """本条不得被当成少做、漏做、stub、TODO 的挡箭牌。"""
    rule = _rule_block()
    assert_contains(
        rule,
        "能不能动手",
        "动手之后必须做到什么程度",
        "不许降级",
        "绝不允许拿本条当少做、漏做、stub、TODO、假实现的挡箭牌",
    )


def test_takes_precedence_over_other_defaults():
    """本条属保护性约束，优先于其余默认约定与效率/进度考量。"""
    rule = _rule_block()
    assert_contains(
        rule,
        "本条的效力",
        "安全与保护性约束",
        "优先于",
        "任何效率、进度、上下文、收益、工期方面的考量",
        "任何宽松解读均无效",
    )


def test_collaboration_section_has_same_level_redline():
    """协作与安全章节须并列声明与安全红线同级的一票否决。"""
    text = read_prompt()
    assert_contains(
        text,
        "未授权改代码（一票否决，与安全红线同级）",
        "后果与触犯安全红线相同",
        "该条为唯一权威口径",
    )


def test_delivery_requires_zero_change_check():
    """交付前必须自检零改动，未授权改动会让整轮交付失败。"""
    text = read_prompt()
    assert_contains(
        text,
        "零改动自检",
        "不得产生任何文件改动",
        "回滚到改动前原文",
        "整轮交付直接判定失败",
    )
