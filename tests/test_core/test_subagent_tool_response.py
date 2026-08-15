"""SubAgent._handle_tool_calls 工具结果完整性防御测试。

背景：ToolScheduler 调度结果可能因极端时序/并发而缺失某个 tool_call
的结果。若不补发，下一轮模型调用会携带「assistant 带 tool_calls 但无
对应 tool 消息」的不完整历史，触发 API 400：
  "An assistant message with 'tool_calls' must be followed by tool messages
   responding to each 'tool_call_id'"

本测试验证：schedule 返回缺失结果时，SubAgent 自动为缺失的 tool_call
补发失败 tool 消息，保证消息序列自洽。
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.subagent import SubAgent
from src.core.tool_executor_async import ToolScheduler


def _make_subagent() -> SubAgent:
    """构造最小可用的 SubAgent（parent 全部 mock）。"""
    parent = MagicMock()
    parent.get_tool_registry.return_value = MagicMock()
    parent.model = "test-model"
    parent._event_port = MagicMock()
    parent._async_model_port = None
    prompt_builder = MagicMock()
    prompt_builder.build_map_agent_prompt.return_value = ["system"]
    prompt_builder.build_review_agent_prompt.return_value = ["system"]
    prompt_builder.build_plan_agent_prompt.return_value = ["system"]
    prompt_builder.build_execute_agent_system_prompt.return_value = ["system"]
    prompt_builder.build_subagent_prompt.return_value = ["system"]
    parent.get_prompt_builder_port.return_value = prompt_builder

    return SubAgent("test-agent", "desc", "prompt", parent)


def test_handle_tool_calls_backfills_missing_result():
    agent = _make_subagent()
    scheduler = MagicMock()
    scheduler.schedule = AsyncMock(return_value=[
        ("call_2", "ok", True),
    ])
    tool_calls = [
        {"id": "call_1", "name": "read_file", "arguments": {"path": "a"}},
        {"id": "call_2", "name": "read_file", "arguments": {"path": "b"}},
    ]
    with patch.object(ToolScheduler, "default", return_value=scheduler):
        asyncio.run(agent._handle_tool_calls("思考", tool_calls, "reason"))

    # messages: system + user + assistant + tool(call_1 补发) + tool(call_2)
    roles = [m["role"] for m in agent.messages]
    assert roles == ["system", "user", "assistant", "tool", "tool"]

    tool_msgs = [m for m in agent.messages if m["role"] == "tool"]
    assert {m["tool_call_id"] for m in tool_msgs} == {"call_1", "call_2"}

    # call_1 是补发的失败结果（调度器未返回）
    call_1 = next(m for m in tool_msgs if m["tool_call_id"] == "call_1")
    assert "失败" in call_1["content"]


def test_handle_tool_calls_no_duplicate_when_complete():
    """schedule 返回全部结果时，不补发任何消息。"""
    agent = _make_subagent()
    scheduler = MagicMock()
    scheduler.schedule = AsyncMock(return_value=[
        ("call_1", "ok1", True),
        ("call_2", "ok2", True),
    ])
    tool_calls = [
        {"id": "call_1", "name": "read_file", "arguments": {"path": "a"}},
        {"id": "call_2", "name": "read_file", "arguments": {"path": "b"}},
    ]
    with patch.object(ToolScheduler, "default", return_value=scheduler):
        asyncio.run(agent._handle_tool_calls("思考", tool_calls, "reason"))

    tool_msgs = [m for m in agent.messages if m["role"] == "tool"]
    assert len(tool_msgs) == 2
    assert all("失败" not in m["content"] for m in tool_msgs)


def test_handle_tool_calls_empty_id_not_backfilled():
    """空 id 的 tool_call 不补发（无法配对，避免引入新问题）。"""
    agent = _make_subagent()
    scheduler = MagicMock()
    scheduler.schedule = AsyncMock(return_value=[])
    tool_calls = [
        {"id": "", "name": "read_file", "arguments": {"path": "a"}},
    ]
    with patch.object(ToolScheduler, "default", return_value=scheduler):
        asyncio.run(agent._handle_tool_calls("思考", tool_calls, "reason"))

    tool_msgs = [m for m in agent.messages if m["role"] == "tool"]
    assert len(tool_msgs) == 0
