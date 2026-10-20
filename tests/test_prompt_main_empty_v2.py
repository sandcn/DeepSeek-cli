"""空模式系统提词（精简补全版）的结构与语义回归测试。

新版提词以「识别 → 计划 → 实施 → 验证 → 交付」为主线，保留精简风格，
同时补齐角色、工具边界、验证流程、协作安全与交付输出。本文件只做
跨章节的整体约束校验；分项语义由同目录既有测试文件覆盖。
"""

from __future__ import annotations

import pytest

from tests._prompt_semantics import assert_contains, prompt_bullets, read_prompt

HEADINGS = (
    "# 全局约束",
    "## 语言与风格",
    "## 工作方式",
    "## 工具与验证",
    "## 协作与安全",
    "## 交付输出",
)


@pytest.fixture(scope="module")
def text() -> str:
    return read_prompt()


def test_prompt_is_concise(text: str):
    """保持精简：全文不超过 150 行。

    原上限为 50 行；其后陆续补入「验证时序红线」「bash 禁改文件红线」
    「bash 替代内部工具红线」等最高优先级条款（规则完整性优先于篇幅），
    上限先放宽到 80；再补入「修改后 bash_opt 实操三查红线」后放宽到 90；
    2026-10 又补入第零条禁令（无授权禁止改代码）、工具调用并发强制、
    修复无限次尝试、方向性选择必须 user_select、跨模块缺陷当场修复等
    红线；随后统一了优先级阶梯、豁免口径与例外条款，提词为 130 行——
    上限相应放宽到 150，仍用于防止提词无限膨胀。
    """
    assert len(text.splitlines()) <= 150


def test_headings_present_and_ordered(text: str):
    """章节标题齐全且顺序稳定。"""
    positions = [text.index(heading) for heading in HEADINGS]
    assert positions == sorted(positions)


def test_no_duplicate_bullets():
    """条目行不得完全重复。"""
    bullets = prompt_bullets()
    assert len(bullets) == len(set(bullets))


def test_core_flow_present(text: str):
    """基础流程与用户指令优先级须明确。"""
    assert_contains(
        text,
        "用户当次的明确指令优先",
        "认知 → 计划 → 实施 → 验证",
    )


def test_core_semantic_clusters_present(text: str):
    """跨章节的核心语义簇须完整。"""
    assert_contains(
        text,
        "简体中文",
        "先认知再动手",
        "先计划再执行",
        "完整交付",
        "优先复用现有代码",
        "bash_opt",
        "单元测试",
        "完成标准",
        "user_select",
        "没有用户的明确指定",
        "commit",
        "元文件",
        "skill",
        "验证结论",
    )


def test_no_legacy_red_line_inflation(text: str):
    """精简版不再使用旧版高密度的「红线 · 一票否决」标注。"""
    assert "红线 · 一票否决" not in text
