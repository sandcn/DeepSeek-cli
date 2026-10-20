"""空模式提词：改完代码必须 bash_opt 实操，并按显示/逻辑/流程三查。

用户要求：修改代码后一定要用 bash_opt 操作一次，分析显示、逻辑、流程
功能有没有问题；有问题立即修复，并以最严重的语气声明为不可豁免的红线。
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
    # 子项既有「  - 」条目，也有「    1. 」这种缩进有序列表（例外举证四类），
    # 统一按「两空格缩进」收集，遇到顶格内容才结束。
    for line in lines[start + 1:]:
        if not line.startswith("  "):
            break
        block.append(line)
    return "\n".join(block)


def test_redline_declares_highest_severity():
    """红线以最严重语气声明：零容忍、无豁免、一票否决、直接判失败。"""
    rule = _rule_block()
    assert_contains(
        rule,
        "最高优先级",
        "零容忍",
        "不可协商",
        "无豁免权",
        "一票否决",
        "硬性红线",
        "直接判定失败",
        "不容辩解",
        "不予受理",
    )


def test_requires_real_run_via_bash_opt():
    """必须真的跑、真的操作：bash 后台启动 + bash_opt 交互 + read_image 看图。"""
    rule = _rule_block()
    assert_contains(
        rule,
        "`bash_opt`",
        "真正跑起来",
        "实际操作",
        "background=true",
        "task_id",
        "`read_image`",
    )


def test_checks_display_logic_flow():
    """三项核查齐全：显示（渲染/布局/文案）、逻辑（数据/边界/异常）、流程（端到端）。"""
    rule = _rule_block()
    assert_contains(
        rule,
        "显示核查",
        "逻辑核查",
        "流程核查",
        "错位",
        "重叠",
        "遮挡",
        "截断",
        "乱码",
        "边界",
        "异常",
        "端到端",
        "启动",
        "退出 / 重进",
    )


def test_fix_immediately_and_recheck():
    """查出的问题必须当场修复并重新实操复核，禁止留待以后。"""
    rule = _rule_block()
    assert_contains(
        rule,
        "当场修复",
        "重新实操复核",
        "重新走一遍",
        "确认问题消失且没有引入新问题",
        "先记下来",
        "已知问题留待以后",
        "下次再优化",
        "不影响主要功能",
    )


def test_test_pass_cannot_replace_manual_run():
    """编译通过 / 单元测试全绿 / 日志干净都不能替代实操三查。"""
    rule = _rule_block()
    assert_contains(
        rule,
        "编译通过",
        "单元测试全绿",
        "日志里没报错",
        "不能替代实操三查",
        "只截一张图",
    )


def test_exception_requires_hard_evidence():
    """无入口/环境不支持等例外必须给出尝试命令、报错原文与不可绕过分析。"""
    rule = _rule_block()
    assert_contains(
        rule,
        "不存在任何可执行入口",
        "已尝试的具体命令",
        "完整报错原文",
        "不可绕过",
        "最小复现脚本",
        "不构成例外",
    )


def test_sections_and_delivery_reference_redline():
    """红线计入本节声明、协作安全同级一票否决、交付与完成标准均须引用。"""
    text = read_prompt()
    assert_contains(
        text,
        "本节含四条最高优先级硬红线",
        "实操三查缺失（一票否决，违反后果与安全红线相同，裁决层级次于安全红线）",
        "交付说明必须附实操三查证据",
        "已按「修改后 bash_opt 实操三查红线」实际操作运行",
    )
