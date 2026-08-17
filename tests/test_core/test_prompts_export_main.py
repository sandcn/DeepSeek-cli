"""验证 prompts_export_main.md 的调度规则：整个计划由一个 execute Agent 执行 + review 后修复强制串行。

本次文档修改要求：
- 执行计划时不再按「每 4 大步骤一批」分批派发 execute；
- 整个计划强制由 1 个 execute Agent 一次性执行全部步骤；
- review 发现的 P0-P3 问题修复必须通过 execute（子类型B）执行且多个修复任务强制串行。
本测试对文档关键规则文本做存在性与一致性断言。
"""

from pathlib import Path

PROMPTS_MAIN = Path(__file__).resolve().parents[2] / "prompts" / "prompts_export_main.md"

# 本次修改必须落地的关键规则文本片段（顺序敏感地组合校验）
REQUIRED_RULES = [
    # 参数速查表：execute 批次大小（整个计划 1 个 agent 一次性执行）
    "execute 批次大小 | 整个计划由 1 个 execute Agent 一次性执行",
    # 参数速查表：Review后修复串行
    "Review后修复串行 | 强制串行，禁止并行 | execute（子类型B）/ review",
    # 调度规则速查：execute 串行（强制）覆盖场景一与 Review 后修复
    "execute 串行（强制）",
    "场景一：整个计划强制由 1 个 execute Agent 一次性串行执行全部步骤，禁止分批派发",
    "Review后修复（子类型B）：强制串行，一个修复任务完成后再派下一个",
    "两者均禁止并行",
    # plan 章节：选「执行」后一次性派发整个计划
    "选「执行」→ 派发 1 个 execute Agent 一次性执行整个计划全部步骤",
    # review 章节：修复必须通过 execute 且串行
    "修复必须通过 `subagent(type=\"execute\")`（子类型B：Review后修复子任务）",
    "多个修复任务强制串行派发，一个修复完成并确认后再派下一个，禁止并行",
    # execute 章节：整个计划 1 个 agent 一次性执行
    "整个计划强制由 **1 个 execute Agent 一次性执行全部步骤**",
    "禁止分批派发多个 execute Agent",
    # execute 章节：子类型B 强制串行
    "子类型B（Review后修复子任务）",
    "**强制串行**——多个修复任务按序逐个派发，一个完成后再派下一个，禁止并行",
    # execute 章节：prompt 严格等于整个计划路径
    "prompt **严格等于** `计划文件: <路径>`",
    # 工作流·执行阶段：整个计划 1 个 agent 一次执行
    "整个计划强制由 **1 个 execute Agent 一次执行**（参数速查）",
]

# 不得残留的过时表述：按 4 步一批分批派发 / 串行限制仅限场景一
OUTDATED_TEXT = "串行限制仅限场景一"
OUTDATED_BATCH_TEXT = "每 4 大步骤一批"
OUTDATED_BATCH_TEXT_2 = "每 4 步骤一批"


def _doc_text() -> str:
    assert PROMPTS_MAIN.is_file(), f"文档不存在: {PROMPTS_MAIN}"
    return PROMPTS_MAIN.read_text(encoding="utf-8")


def test_whole_plan_single_execute_rule_present():
    """整个计划由 1 个 execute Agent 一次性执行的规则必须全部存在。"""
    text = _doc_text()
    missing = [rule for rule in REQUIRED_RULES if rule not in text]
    assert not missing, f"文档缺少以下关键规则文本: {missing}"


def test_no_batch_dispatch_text():
    """不得再出现「每 4 大步骤一批」等分批派发表述。"""
    text = _doc_text()
    assert OUTDATED_BATCH_TEXT not in text, f"文档仍残留分批派发表述: {OUTDATED_BATCH_TEXT}"
    assert OUTDATED_BATCH_TEXT_2 not in text, f"文档仍残留分批派发表述: {OUTDATED_BATCH_TEXT_2}"


def test_no_outdated_serial_scope_text():
    """不得再出现「串行限制仅限场景一」的过时表述。"""
    text = _doc_text()
    assert OUTDATED_TEXT not in text, f"文档仍残留过时表述: {OUTDATED_TEXT}"


def test_subtype_b_serial_consistency():
    """子类型A 可并行、子类型B 强制串行，两者表述互斥且一致。"""
    text = _doc_text()
    # 子类型A：仍可按批并行（不适用 execute 串行）
    assert "不适用「execute 串行」限制，可按批并行" in text
    # 子类型B：强制串行（禁止并行）
    assert "强制串行" in text
    assert "禁止并行" in text
