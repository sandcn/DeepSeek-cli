"""主 Agent 工具可用性回归测试。

根因回归：tool_policy 的排除表只约束 SubAgent 类型（map/review/plan/execute）。
主 Agent 不设 agent_type（None），修复前 policy 的 pre-execute 钩子把 None 回退
为 "execute" → 主 Agent 的 user_select / web_search / subagent / subagent_opt
被误判为 execute 型 SubAgent 而被拒绝（用户现象：main agent 用不了 user_select）。
"""

from __future__ import annotations

import pytest

from src.plugins.bootstrap import build_kernel, shutdown_kernel
from src.tools.base import Func
from src.tools.tool_policy import TOOL_EXCLUSION_MAP, get_excluded_tools

_MAIN_RESTRICTED_IN_EXECUTE = ("user_select", "web_search", "subagent", "subagent_opt")


@pytest.fixture
async def cli_kernel():
    kernel = await build_kernel("cli")
    try:
        yield kernel
    finally:
        await shutdown_kernel(kernel)


def test_get_excluded_tools_none_is_empty():
    """agent_type 为 None/空（主 Agent）→ 返回空集，不受子代理排除表约束。"""
    assert get_excluded_tools(None) == set()
    assert get_excluded_tools("") == set()


def test_get_excluded_tools_execute_unchanged():
    """明确的 execute 子代理类型仍按表排除（不因主 Agent 修复而放松）。"""
    excluded = get_excluded_tools("execute")
    for name in _MAIN_RESTRICTED_IN_EXECUTE:
        assert name in excluded
    assert "read_file" not in excluded
    assert "bash" not in excluded


def test_exclusion_map_keys_are_subagent_types():
    assert set(TOOL_EXCLUSION_MAP) == {"map", "review", "plan", "execute"}


def test_func_can_use_main_agent_allows_restricted_tools():
    """Func.can_use(agent_type=None)（主 Agent）放行 execute 型被排除的工具。"""
    for name in _MAIN_RESTRICTED_IN_EXECUTE:
        allowed, reason = Func.can_use(name, None)
        assert allowed is True, f"{name} 应为主 Agent 放行，实际: {reason}"


def test_func_can_use_execute_still_denies():
    allowed, reason = Func.can_use("user_select", "execute")
    assert allowed is False
    assert "execute" in reason


def test_policy_service_main_agent_allows(cli_kernel):
    policy = cli_kernel.root.policy
    assert policy.excluded_tools(None) == set()
    assert policy.excluded_tools("") == set()
    for name in _MAIN_RESTRICTED_IN_EXECUTE:
        allowed, reason = policy.check(name, None)
        assert allowed is True, f"{name} 应为主 Agent 放行，实际: {reason}"


def test_policy_service_subagent_types_still_deny(cli_kernel):
    policy = cli_kernel.root.policy
    assert policy.excluded_tools("execute") >= set(_MAIN_RESTRICTED_IN_EXECUTE)
    allowed, reason = policy.check("user_select", "execute")
    assert allowed is False and "execute" in reason


async def test_pre_execute_allows_main_agent_user_select(cli_kernel):
    """主 Agent（agent_type=None）的 user_select 不被 pre-execute 拒绝。"""
    policy = cli_kernel.root.policy
    reached = []

    async def _next():
        reached.append(True)
        return {"allow": True}

    call = {"name": "user_select", "agent_type": None, "arguments": {"title": "t"}}
    decision = await policy._on_pre_execute(call, _next)
    assert not (isinstance(decision, dict) and decision.get("allow") is False)
    assert reached == [True]


async def test_pre_execute_denies_execute_subagent_user_select(cli_kernel):
    """execute 型 SubAgent 的 user_select 仍被 pre-execute 拒绝。"""
    policy = cli_kernel.root.policy

    async def _next():
        return {"allow": True}

    call = {"name": "user_select", "agent_type": "execute", "arguments": {"title": "t"}}
    decision = await policy._on_pre_execute(call, _next)
    assert isinstance(decision, dict)
    assert decision.get("allow") is False
    assert "execute" in decision.get("reason", "")


async def test_main_agent_schema_exposes_user_select(cli_kernel):
    """主 Agent 工具 schema 含 user_select（与策略放行一致）。"""
    agent = cli_kernel.root.agent_loop.make_event_agent()
    names = {s["function"]["name"] for s in agent.tools}
    assert "user_select" in names
    assert not hasattr(agent, "agent_type")
