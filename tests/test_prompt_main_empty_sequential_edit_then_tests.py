"""空模式提词：一次性连续改完代码、跑通流程后再补单元测试。

用户要求：修改代码要一口气连续改完所有代码，不要边修改边验证；
流程与测试跑通、确认无问题后，再增加单元测试；条款之间不得有逻辑冲突。
"""

from __future__ import annotations

from tests._prompt_semantics import assert_contains, read_prompt


def _bullet(prefix: str) -> str:
    """返回以 ``prefix`` 开头的条目及其全部「  - 」缩进子项（缺失即失败）。"""
    lines = read_prompt().splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith(prefix)), None)
    assert start is not None, f"提示词缺少条目: {prefix}"
    block = [lines[start]]
    for line in lines[start + 1 :]:
        if not line.startswith("  - "):
            break
        block.append(line)
    return "\n".join(block)


def _timing_rule() -> str:
    return _bullet("- **验证时序红线")


def _line(prefix: str) -> str:
    """返回以 ``prefix`` 开头的单行（缺失即失败）。"""
    match = next((line for line in read_prompt().splitlines() if line.startswith(prefix)), None)
    assert match is not None, f"提示词缺少行: {prefix}"
    return match


def _test_rule() -> str:
    return _bullet("- **测试（强制）**")


def test_timing_rule_is_three_stage_and_ordered():
    """验证时序明确为「实施 → 流程验证 → 补单元测试」三阶段且不得颠倒。"""
    assert_contains(
        _timing_rule(),
        "三阶段，不得颠倒",
        "①**实施**",
        "②**流程验证**",
        "③**补单元测试**",
    )


def test_all_implementation_changes_done_in_one_pass():
    """实施阶段必须一次性连续改完全部源码 / 配置 / 脚本，禁止改一处验一处。"""
    assert_contains(
        _timing_rule(),
        "全部源码 / 配置 / 脚本一次性连续改完",
        "禁止改一处验一处",
        "禁止每改一个文件就跑构建 / 编译 / 测试 / 程序",
    )


def test_no_unit_tests_during_modification_phase():
    """修改过程中不得顺手补单元测试、边改边跑。"""
    assert_contains(_timing_rule(), "禁止在修改过程中顺手补单元测试")
    assert_contains(
        _line("  - **严禁半途验证**"),
        "修改过程中顺手补单元测试、边改边跑测试，同属本条禁止",
    )


def test_flow_verification_precedes_unit_tests():
    """测试条目顺序固定：先跑通流程，再新增 / 更新单元测试。"""
    block = _test_rule()
    assert_contains(block, "顺序固定：一次改完全部代码 → 跑通流程 → 最后补单元测试")
    assert block.index("跑通流程") < block.index("新增或更新对应测试"), "单元测试必须在跑通流程之后再补"


def test_order_cannot_be_reversed():
    """颠倒顺序属违规，且不得以未写单元测试为由推迟流程实操。"""
    assert_contains(
        _test_rule(),
        "禁止颠倒该顺序",
        "不得顺手写测试、边改边跑",
        "不得以「单元测试还没写」为由跳过或推迟流程实操",
    )


def test_phase_three_only_touches_test_files():
    """补测试阶段只允许新增 / 更新测试文件，不得夹带源码改动。"""
    block = _timing_rule()
    assert_contains(
        block,
        "只允许新增 / 更新测试文件与必要测试夹具",
        "不得夹带任何源码 / 配置 / 脚本改动",
        "②中发现问题需要改源码时，必须回到①一次性改完再重新验证",
    )


def test_existing_test_contracts_still_hold():
    """与既有测试条款不冲突：测试惯例、实操优先、完成标准仍须补齐测试。"""
    assert_contains(
        read_prompt(),
        "按项目既有测试目录与语言惯例新增或更新对应测试",
        "单元测试全绿也不能替代实操",
        "补齐单元测试并运行通过",
    )
