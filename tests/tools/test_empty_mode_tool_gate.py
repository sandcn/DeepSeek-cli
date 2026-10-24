"""空模式工具门禁 — `subagent` / `subagent_opt` 必须在 schema 层移除。

空模式提词规定「单体执行范式，默认不派发子代理」，但主 Agent 不设
agent_type、`standard` preset 也不排除这两项工具。本测试锁定工具层兜底：
`Agent._get_active_tools` 在空模式下过滤掉派生子代理的工具，切回标准模式
后恢复（Ctrl+B 切换后下一次请求即生效）。
"""

from __future__ import annotations

from src.core.agent import Agent
from src.prompt_builder import builder
from src.tools.tool_policy import filter_schemas_for_empty_mode


def _schema(name: str) -> dict:
    return {"type": "function", "function": {"name": name, "description": ""}}


def test_filter_schemas_for_empty_mode_removes_delegation_tools() -> None:
    schemas = [
        _schema("read"),
        _schema("subagent"),
        _schema("subagent_opt"),
        _schema("user_select"),
    ]

    names = [s["function"]["name"] for s in filter_schemas_for_empty_mode(schemas)]

    assert names == ["read", "user_select"]


def test_filter_schemas_for_empty_mode_keeps_empty_input() -> None:
    assert filter_schemas_for_empty_mode(None) == []
    assert filter_schemas_for_empty_mode([]) == []


def test_agent_active_tools_follow_current_mode() -> None:
    """空模式移除子代理工具；切回标准模式后恢复（模式是运行期真源）。"""
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
        assert "read" in names
        assert "subagent" not in names
        assert "subagent_opt" not in names

        builder.set_mode("standard")
        names = {s["function"]["name"] for s in agent._get_active_tools()}
        assert {"read", "subagent", "subagent_opt"} <= names
    finally:
        builder.set_mode(original)
