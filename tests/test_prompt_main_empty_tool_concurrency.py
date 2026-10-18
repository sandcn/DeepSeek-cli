"""空模式提词：工具调用并发强制规则。

用户要求：在 prompts_export_main_empty.md 中明确「能并发调用工具就强制并发调用工具」，
禁止把相互独立、无依赖的工具调用拆成多次串行执行；同时保留必须串行的依赖边界，
且不得突破既有的验证时序红线。
"""

from __future__ import annotations

from tests._prompt_semantics import assert_contains, read_prompt


def _rule_block() -> str:
    """返回「工具调用并发」条目及其全部缩进子条目。"""
    lines = read_prompt().splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line.startswith("- **工具调用并发")),
        None,
    )
    assert start is not None, "缺少「工具调用并发」条目"
    block = [lines[start]]
    for line in lines[start + 1:]:
        if not line.startswith("  - "):
            break
        block.append(line)
    return "\n".join(block)


def test_rule_is_mandatory_not_advisory():
    """规则必须是强制要求：同一轮一次性并发发起，禁止逐个串行。"""
    rule = _rule_block()
    assert_contains(
        rule,
        "强制",
        "零豁免",
        "必须放在同一轮一次性并发发起",
        "禁止拆成多次串行调用逐个执行",
        "不设人为上限",
        "不得为了「稳一点」「看清楚结果」而人为拆批",
    )


def test_independent_calls_identified_by_dependency_checks():
    """并发前提写清楚：相互独立、无数据依赖、无执行顺序要求、无写冲突。"""
    rule = _rule_block()
    assert_contains(
        rule,
        "相互独立",
        "无数据依赖",
        "无执行顺序要求",
        "无写冲突",
    )


def test_lists_mandatory_concurrency_scenarios():
    """多文件读取、独立搜索、独立检索、只读查询、不同文件编辑等必须并发。"""
    rule = _rule_block()
    assert_contains(
        rule,
        "必须并发的情形",
        "读取多个已知路径的文件",
        "`read`",
        "多个互不依赖的搜索与查找",
        "`grep`/`glob`/`ls`",
        "多个独立目标的检索",
        "`web_search`/`web_fetch`",
        "多个只读查询",
        "`git status`/`git diff`/`git log`",
        "多个不同文件的独立编辑",
        "`edit`/`write`",
        "一律并发，禁止逐个串行",
    )


def test_serial_calls_limited_to_real_dependencies():
    """只有结果依赖、写冲突、状态依赖才允许串行，其余串行按违规处理。"""
    rule = _rule_block()
    assert_contains(
        rule,
        "必须串行的情形（仅限这些）",
        "参数或目标来自前序调用的结果",
        "先 `grep`/`glob` 定位、再 `read`",
        "先 `read`、再 `edit`",
        "写冲突或共享可变状态",
        "先后状态依赖",
        "任何串行调用都按违规处理",
    )


def test_parallel_batch_must_be_truly_independent():
    """并发批次不得互相依赖、不得并发写同一文件、不得借并发省略检查。"""
    rule = _rule_block()
    assert_contains(
        rule,
        "并发调用必须真正独立",
        "不得互相依赖结果",
        "不得并发针对同一文件的写操作",
        "不得借并发省略本应分别执行的检查",
        "只重试失败项",
        "不得因此把整批改为串行",
        "不得用并发掩盖失败",
    )


def test_does_not_break_verification_ordering_redline():
    """并发不得突破阶段顺序与验证时序红线。"""
    rule = _rule_block()
    assert_contains(
        rule,
        "不得突破阶段顺序",
        "「认知 → 计划 → 实施 → 验证」的顺序",
        "代码改动全部落盘前",
        "验证性 `bash`/`bash_opt` 调用不得发起",
        "更不得与写操作并发",
        "验证时序红线",
    )


def test_rejects_serialization_excuses_and_requires_rework():
    """禁止以稳定性、失败担忧、结果等价等借口拆成串行；违规须改正重做。"""
    rule = _rule_block()
    assert_contains(
        rule,
        "禁止借口",
        "逐个更稳",
        "怕工具失败",
        "方便看结果",
        "调用不多",
        "不确定能否并发",
        "能独立就必须并发",
        "流程违规",
        "必须改正并重新执行受影响的调用",
        "结果一样",
        "反正不慢",
    )


def test_rule_placed_in_working_style_section():
    """并发规则属于工作方式，须位于「工具与验证」章节之前。"""
    text = read_prompt()
    rule_pos = text.index("- **工具调用并发")
    verification_pos = text.index("## 工具与验证")
    assert 0 < rule_pos < verification_pos, "「工具调用并发」应位于「工作方式」章节内"
