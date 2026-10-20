"""空模式系统提词：分析代码必须输出从程序入口开始的全量调用链。

用户要求：分析代码时要输出从程序入口开始的全量调用链，且加入时不得引入
逻辑冲突。本文件固化该条款的语义要点，并校验它与「先认知再动手」
「按调用链通读」「结论先行」「第零条禁令」「交付输出简短」等既有条款
之间的衔接表述，避免后续改动把「输出调用链」写成「替代通读」或
「获得写权限」之类的矛盾口径。适用范围以当前工作目录（项目根）内的代码
为准；对当前工作目录之外的代码只要求写清与项目内调用点的交界，不强制
输出其内部链路。
"""

from __future__ import annotations

import pytest

from tests._prompt_semantics import assert_contains, read_prompt


@pytest.fixture(scope="module")
def text() -> str:
    return read_prompt()


def _work_style_section(text: str) -> str:
    """截取「## 工作方式」章节（到下一个二级标题为止）。"""
    start = text.index("## 工作方式")
    end = text.index("## 工具与验证")
    return text[start:end]


def test_rule_present_with_completeness_clauses(text: str):
    """条款存在，且「全量」有可执行的完整性口径。"""
    assert_contains(
        text,
        "全量调用链",
        "从程序真实入口到目标代码",
        "不得从「目标函数的直接调用方」起算",
        "逐条给出",
        "不得编造链路",
    )


def test_entry_points_enumerated(text: str):
    """入口口径要具体可判定，不能只说「入口」。"""
    assert_contains(
        text,
        "`main`",
        "CLI 命令与子命令分派",
        "路由注册",
        "事件 / 回调 / 信号绑定",
        "定时任务与 worker 注册",
        "公开 API / 导出点 / 被测出口",
    )


def test_rule_lives_in_working_style_after_call_chain_read(text: str):
    """条款落在「工作方式」，且排在「按调用链通读」之后（读取 → 输出）。"""
    section = _work_style_section(text)
    assert "全量调用链" in section
    assert section.index("按调用链通读") < section.index("全量调用链")


def test_no_conflict_with_existing_rules(text: str):
    """与既有条款的衔接口径必须显式写出，避免产生逻辑冲突。"""
    assert_contains(
        text,
        "本条约束**输出**",
        "与「先认知再动手」「按调用链通读」的**读取**要求相互独立、同时生效",
        "不因输出调用链而获得任何写权限",
        "本条不改变「结论先行」",
        "交付说明仍按「交付输出」保持简短",
    )


def test_scope_excludes_non_analysis_tasks(text: str):
    """适用范围限定在代码分析，纯问答不强制输出调用链。"""
    assert_contains(
        text,
        "与代码分析无关的任务不要求输出调用链",
        "无可达入口",
    )


def test_scope_is_current_working_directory(text: str):
    """范围以当前工作目录（项目根）内的代码为准，外部代码只要求交界。"""
    assert_contains(
        text,
        "当前工作目录（即项目根，按「工具与验证 · 文件操作」条确定）内代码",
        "分析对象位于当前工作目录之外",
        "必须写清它与当前工作目录内调用点的交界",
        "不得以「外部代码」为由省略项目内的完整路径",
    )

