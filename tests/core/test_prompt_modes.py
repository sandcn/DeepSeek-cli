"""主 Agent 运行模式与静态提词契约测试。

守护「空 / 简单 / 标准」三模式的真实语义：三者是**三种提词范式**，不是
规则量递增的三档——`empty` 为单体完整规则集（规则量最大）、`simple` 为
命令式精简规则集（最小）、`standard` 为子代理编排范式。

同时锁定两处已修复的规则文本矛盾（防回归）：
  - 「零落盘时不得声明完成」与「纯问答/审查轮以结论完整为标准」互相否定；
  - 条款自述「裁决层级次于安全红线」与唯一阶梯（含第零条/实现完整性红线）不一致。
"""

from __future__ import annotations

import pytest

from src.prompt_builder import builder
from src.prompt_builder.modes import DEFAULT_MODE, mode_order, resolve_mode_export

#: 模式 → (提词文件名, 该范式的身份标记)
MODE_PROMPTS = (
    ("empty", "prompts_export_main_empty", "第零条禁令"),
    ("simple", "prompts_export_main_simple", "强势禁止"),
    ("standard", "prompts_export_main", "Agent 总览"),
)


def test_default_mode_is_empty() -> None:
    assert DEFAULT_MODE == "empty"
    assert builder.get_mode() == "empty"


def test_mode_cycle_order_is_empty_simple_standard() -> None:
    assert mode_order() == ["empty", "simple", "standard"]


@pytest.mark.parametrize(("mode", "export", "_marker"), MODE_PROMPTS)
def test_mode_export_mapping(mode: str, export: str, _marker: str) -> None:
    assert resolve_mode_export(mode) == export


@pytest.mark.parametrize(("mode", "export", "marker"), MODE_PROMPTS)
def test_each_mode_loads_its_monolithic_prompt(mode: str, export: str, marker: str) -> None:
    content = builder._load_prompt(export)
    assert content.strip(), f"{mode} 模式提词为空"
    assert marker in content, f"{mode} 模式提词缺少身份标记：{marker}"


def test_three_prompts_are_distinct_paradigms() -> None:
    contents = [builder._load_prompt(export) for _mode, export, _m in MODE_PROMPTS]
    assert len(set(contents)) == len(contents)


def test_build_system_prompt_uses_current_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    original = builder.get_mode()
    try:
        builder.set_mode("empty")
        parts = builder.build_system_prompt()
        assert parts
        assert "第零条禁令" in parts[0]
    finally:
        builder.set_mode(original)


def test_removed_contradictions_do_not_come_back() -> None:
    content = builder._load_prompt("prompts_export_main_empty")
    # 「零落盘时不得声明完成」与「纯问答/审查不适用、不得据此判定未完成」互斥
    assert "零落盘时不得声明完成" not in content
    # 条款自述层级不得与唯一阶梯冲突（须写「裁决层级见……」）
    assert "裁决层级次于安全红线" not in content


def test_prompt_contradictions_fixed_in_empty_mode() -> None:
    """锁定空模式提词本轮修复的授权、元文件与配置测试口径。"""
    content = builder._load_prompt("prompts_export_main_empty")

    # 空选择不得被当成默认全选授权；拒绝语义统一为 cancel / 空选择。
    assert "主动拒绝或取消" not in content
    assert "`selected` 为空时按默认全选" not in content
    assert "`selected` 为空视为未授权" in content
    assert "法定拟制授权" in content

    # 互斥选项不得同时全选。
    assert "不因方案互斥而少勾" not in content

    # 提词文件按配置跑受影响测试，不强制图形界面实操。
    assert "`prompts/*.md` 提词文件按配置处理" in content
    assert "不要求图形界面实操" in content

    # 元文件注入事实必须与实现一致。
    assert "7 个运行时元文件" not in content
    assert "`_empty` 空环境" not in content
    assert "不会自动进入主 Agent 上下文" in content

    # 超时授权只保留安全红线约束，不得声称只剩凭据一项。
    assert "唯一保留的约束是不外泄凭据值" not in content
    assert "当场多选" not in content
    assert "当场申请授权" not in content
