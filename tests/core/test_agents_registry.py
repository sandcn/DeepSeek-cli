"""Agent 注册表与 agent/* 事件域测试 — ctx.agents（对应 dsh core/agent）。

覆盖：
- 活跃 Agent 登记 / 注销 / 查询（id / kind / session / 父 Agent）；
- 登记时发出 ``agent/created``，注销时发出 ``agent/destroyed``；
- 每个 Agent 拥有独立作用域，作用域注册互不污染、注销时撤销；
- agent_loop 插件创建 Agent 时自动登记到 ctx.agents；
- 无内核时访问器安全回退。
"""

from __future__ import annotations

import pytest

from src.core.events.agent_types import AgentEventType
from src.kernel import Kernel, plugin
from src.plugins.agents import AgentsService
from src.plugins.bootstrap import build_kernel, shutdown_kernel


@pytest.fixture
async def cli_kernel():
    kernel = await build_kernel("cli")
    try:
        yield kernel
    finally:
        await shutdown_kernel(kernel)


class _FakeAgent:
    def __init__(self, model="deepseek-v4"):
        self.model = model


async def test_register_and_query(cli_kernel):
    agents = cli_kernel.resolve_service("agents")
    agent = _FakeAgent()
    record = agents.register(agent, kind="main", model="deepseek-v4")
    assert record.id.startswith("main-")
    assert agents.count() == 1
    assert agents.get(record.id) is record
    assert agents.of(agent) is record
    assert agents.by_kind("main") == [record]


async def test_register_is_idempotent_per_agent(cli_kernel):
    agents = cli_kernel.resolve_service("agents")
    agent = _FakeAgent()
    first = agents.register(agent, kind="main")
    second = agents.register(agent, kind="main")
    assert first is second
    assert agents.count() == 1


async def test_unregister_removes_record(cli_kernel):
    agents = cli_kernel.resolve_service("agents")
    agent = _FakeAgent()
    record = agents.register(agent, kind="main")
    assert agents.unregister(record) is True
    assert agents.count() == 0
    assert agents.get(record.id) is None
    assert agents.unregister(record) is False


async def test_created_and_destroyed_events(cli_kernel):
    agents = cli_kernel.resolve_service("agents")
    created = []
    destroyed = []
    cli_kernel.bus.on(AgentEventType.CREATED, lambda record: created.append(record))
    cli_kernel.bus.on(AgentEventType.DESTROYED, lambda record: destroyed.append(record))

    agent = _FakeAgent()
    record = agents.register(agent, kind="subagent")
    assert created == [record]
    agents.unregister(record)
    assert destroyed == [record]


async def test_status_event_and_touch(cli_kernel):
    agents = cli_kernel.resolve_service("agents")
    statuses = []
    cli_kernel.bus.on(AgentEventType.STATUS, lambda record, status: statuses.append((record.id, status)))
    record = agents.register(_FakeAgent(), kind="main")
    agents.set_status(record, "running")
    assert record.status == "running"
    assert statuses == [(record.id, "running")]


async def test_agent_scopes_are_isolated(cli_kernel):
    agents = cli_kernel.resolve_service("agents")
    a = agents.register(_FakeAgent(), kind="main")
    b = agents.register(_FakeAgent(), kind="subagent")
    a.scope.provide("persona", "reviewer")
    b.scope.provide("persona", "coder")
    assert a.scope.service("persona") == "reviewer"
    assert b.scope.service("persona") == "coder"
    assert not cli_kernel.has_service("persona")


async def test_scope_closed_on_unregister(cli_kernel):
    agents = cli_kernel.resolve_service("agents")
    record = agents.register(_FakeAgent(), kind="main")
    scope = record.scope
    scope.provide("persona", "reviewer")
    agents.unregister(record)
    assert scope.closed is True
    assert scope.service("persona") is None


async def test_isolated_keys_do_not_fall_through(cli_kernel):
    agents = cli_kernel.resolve_service("agents")
    record = agents.register(_FakeAgent(), kind="main", isolated=["llm"])
    assert record.scope.has("llm") is False
    record.scope.provide("llm", "scoped-llm")
    assert record.scope.service("llm") == "scoped-llm"
    assert cli_kernel.resolve_service("llm") is not None


async def test_session_index(cli_kernel):
    agents = cli_kernel.resolve_service("agents")
    a = agents.register(_FakeAgent(), kind="main", session_id="s1")
    b = agents.register(_FakeAgent(), kind="subagent", session_id="s1", parent=a)
    assert b.parent_id == a.id
    assert {r.id for r in agents.of_session("s1")} == {a.id, b.id}


async def test_shutdown_revokes_all_records():
    kernel = await build_kernel("cli")
    agents = kernel.resolve_service("agents")
    record = agents.register(_FakeAgent(), kind="main")
    scope = record.scope
    scope.provide("x", 1)
    await shutdown_kernel(kernel)
    assert scope.closed is True


async def test_agent_loop_registers_created_agent(cli_kernel):
    loop = cli_kernel.resolve_service("agent_loop")
    agents = cli_kernel.resolve_service("agents")
    before = agents.count()
    agent = loop.create_agent(model="deepseek-v4-pro", kind="main")
    assert agents.count() == before + 1
    record = agents.of(agent)
    assert record is not None
    assert record.kind == "main"
    assert record.model == "deepseek-v4-pro"
    assert getattr(agent, "agent_id", None) == record.id


async def test_agent_loop_unregister(cli_kernel):
    loop = cli_kernel.resolve_service("agent_loop")
    agents = cli_kernel.resolve_service("agents")
    agent = loop.create_headless_agent(model="deepseek-v4")
    assert agents.of(agent) is not None
    assert loop.unregister(agent) is True
    assert agents.of(agent) is None


async def test_agents_service_direct_kernel():
    kernel = Kernel(name="agents-test")

    @plugin("events")
    def events(ctx):
        from src.plugins.events import EventService

        return EventService(ctx)

    kernel.plugin(events)
    kernel.plugin(lambda ctx: AgentsService(ctx))
    await kernel.settle()
    assert kernel.has_service("agents")


def test_kernel_runtime_agents_accessors_without_kernel():
    from src.core.adapters import kernel_runtime as kr

    assert kr.active_agents_service() is None
    assert kr.register_agent(_FakeAgent()) is None


async def test_subagent_registers_into_agents(cli_kernel):
    from src.core.subagent import SubAgent

    loop = cli_kernel.resolve_service("agent_loop")
    agents = cli_kernel.resolve_service("agents")
    parent = loop.create_agent(model="deepseek-v4-pro")
    before = agents.count()
    sub = SubAgent("lbl", "desc", "prompt", parent, agent_type="map")
    assert agents.count() == before + 1
    record = agents.of(sub)
    assert record is not None
    assert record.kind == "map"
    assert record.parent_id == agents.of(parent).id


async def test_subagent_unregisters_from_agents(cli_kernel):
    from src.core.subagent import SubAgent

    loop = cli_kernel.resolve_service("agent_loop")
    agents = cli_kernel.resolve_service("agents")
    parent = loop.create_agent(model="deepseek-v4-pro")
    sub = SubAgent("lbl", "desc", "prompt", parent, agent_type="map")
    assert agents.of(sub) is not None
    sub._unregister_from_agents()
    assert agents.of(sub) is None
