"""空模式工具门禁 — `subagent` / `subagent_opt` 在空模式下必须**可用**。

用户需求（2026-10-25，方案 A）：空模式提词允许「用户明确点名要求时派发
子代理」，工具层不再从 schema 移除这两项；`subagent_opt(op="release")` 还是
主 Agent 文件沙盒锁的唯一主动释放通道，移除会让持锁方无法主动释放。

本测试锁定三点：
1. `EMPTY_MODE_EXCLUDED_TOOLS` 为空集（保留为后续收紧的扩展位）；
2. `filter_schemas_for_empty_mode` 当前不剔除任何工具（含 subagent / subagent_opt）；
3. `Agent._get_active_tools` 在空模式与标准模式下都保留这两项（Ctrl+B
   切换模式不改变其可见性）。
"""

from __future__ import annotations

from src.core.agent import Agent
from src.prompt_builder import builder
from src.tools.tool_policy import (
    EMPTY_MODE_EXCLUDED_TOOLS,
    filter_schemas_for_empty_mode,
)


def _schema(name: str) -> dict:
    return {"type": "function", "function": {"name": name, "description": ""}}


def test_empty_mode_exclusion_set_is_empty() -> None:
    """空模式排除集合为空——收紧门禁须显式向集合填回工具名。"""
    assert EMPTY_MODE_EXCLUDED_TOOLS == frozenset()


def test_filter_schemas_for_empty_mode_keeps_delegation_tools() -> None:
    schemas = [
        _schema("read"),
        _schema("subagent"),
        _schema("subagent_opt"),
        _schema("user_select"),
    ]

    names = [s["function"]["name"] for s in filter_schemas_for_empty_mode(schemas)]

    assert names == ["read", "subagent", "subagent_opt", "user_select"]


def test_filter_schemas_for_empty_mode_keeps_empty_input() -> None:
    assert filter_schemas_for_empty_mode(None) == []
    assert filter_schemas_for_empty_mode([]) == []


def test_agent_active_tools_keep_delegation_tools_in_empty_mode() -> None:
    """空模式下 subagent / subagent_opt 可见；切到标准模式仍可见。"""
    agent = object.__new__(Agent)
    agent.tools = [
        _schema("read"),
        _schema("subagent"),
        _schema("subagent_opt"),
    ]
    agent.model = None

    original = builder.get_mode()
    try:
        builder.set_mode("empty")
        names = {s["function"]["name"] for s in agent._get_active_tools()}
        assert {"read", "subagent", "subagent_opt"} <= names

        builder.set_mode("standard")
        names = {s["function"]["name"] for s in agent._get_active_tools()}
        assert {"read", "subagent", "subagent_opt"} <= names
    finally:
        builder.set_mode(original)
