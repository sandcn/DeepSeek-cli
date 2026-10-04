"""会话日志与投影插件测试 — ctx.session_log / ctx.session_projections。

覆盖：
- 插件提供日志创建 / 恢复 / 快照 / 视图 / 校验；
- 投影 seam 的内置 turnBoundary 与自定义投影注册；
- Agent / SubAgent 的消息视图由日志驱动且满足「模型可见即已记录」；
- ChatSession 关联会话 id 到日志；
- 运行时不变量 agents.messages_recorded。
"""

from __future__ import annotations

import pytest

from src.core.events.agent_types import SessionEventType
from src.kernel import get_current_kernel
from src.plugins.bootstrap import build_kernel, shutdown_kernel


@pytest.fixture
async def cli_kernel():
    kernel = await build_kernel("cli")
    try:
        yield kernel
    finally:
        await shutdown_kernel(kernel)


async def test_session_log_service_roundtrip(cli_kernel):
    service = cli_kernel.resolve_service("session_log")
    log = service.create("s1")
    log.append(SessionEventType.USER_MESSAGE, content="hi")
    log.append(SessionEventType.ASSISTANT_MESSAGE, content="yo")
    assert service.messages(log) == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "yo"},
    ]
    restored = service.restore(service.snapshot(log), session_id="s1")
    assert service.messages(restored) == service.messages(log)


async def test_session_log_service_view(cli_kernel):
    service = cli_kernel.resolve_service("session_log")
    view = service.view(initial=[{"role": "system", "content": "s"}])
    view.append({"role": "user", "content": "u"})
    assert service.verify(view, view.log) is True


async def test_session_log_service_fork(cli_kernel):
    service = cli_kernel.resolve_service("session_log")
    log = service.create()
    log.append(SessionEventType.USER_MESSAGE, content="a")
    log.append(SessionEventType.USER_MESSAGE, content="b")
    forked = service.fork(log, at=1)
    assert service.messages(forked) == [{"role": "user", "content": "a"}]


async def test_session_projections_builtin_turn_boundary(cli_kernel):
    projections = cli_kernel.resolve_service("session_projections")
    assert projections.has("turnBoundary") is True
    log = cli_kernel.resolve_service("session_log").create()
    log.append(SessionEventType.TURN_START)
    log.append(SessionEventType.STEP_START)
    log.append(SessionEventType.TURN_END, interrupted=False)
    state = projections.state_of("turnBoundary", log.events())
    assert state == {"turn": 1, "open": False, "steps": 1, "interrupted": False}


async def test_session_projections_register_custom(cli_kernel):
    projections = cli_kernel.resolve_service("session_projections")
    dispose = projections.register("upper", lambda state, event: (state or 0) + 1, initial=lambda: 0)
    log = cli_kernel.resolve_service("session_log").create()
    log.append(SessionEventType.USER_MESSAGE, content="x")
    assert projections.state_of("upper", log.events()) == 1
    assert projections.snapshot(log.events())["upper"] == 1
    dispose()
    assert projections.has("upper") is False


async def test_agent_messages_view_is_log_driven(cli_kernel):
    loop = cli_kernel.resolve_service("agent_loop")
    agent = loop.create_agent(model="deepseek-v4-pro")
    from src.core.session_log import LoggedMessageList

    assert isinstance(agent.messages, LoggedMessageList)
    assert agent.messages.verify() is True
    agent.messages.append({"role": "user", "content": "hi"})
    assert agent.messages.verify() is True
    assert agent.messages.log.derive_messages()[-1]["content"] == "hi"


async def test_subagent_messages_view_is_log_driven(cli_kernel):
    from src.core.session_log import LoggedMessageList
    from src.core.subagent import SubAgent

    parent = cli_kernel.resolve_service("agent_loop").create_agent(model="deepseek-v4-pro")
    sub = SubAgent("label", "desc", "prompt", parent, agent_type="map")
    assert isinstance(sub.messages, LoggedMessageList)
    assert sub.messages.verify() is True


async def test_chat_session_links_session_id_to_log(cli_kernel):
    session = cli_kernel.resolve_service("sessions").create()
    assert session.session_log is not None
    session.session_id = "s-test"
    assert session.session_log.session_id == "s-test"


async def test_invariant_messages_recorded(cli_kernel):
    invariants = cli_kernel.resolve_service("invariants")
    assert "agents.messages_recorded" in invariants.names()
    assert invariants.check() == []


async def test_invariant_detects_divergence(cli_kernel):
    loop = cli_kernel.resolve_service("agent_loop")
    agent = loop.create_agent(model="deepseek-v4-pro")
    # 人为破坏视图与日志的一致性
    agent.messages._items.append({"role": "user", "content": "ghost"})
    invariants = cli_kernel.resolve_service("invariants")
    failures = invariants.check()
    assert any("模型可见即已记录" in f for f in failures)


def test_agent_messages_log_view_without_kernel():
    from src.core.session_log import LoggedMessageList
    from src.tools.registry import ToolRegistry
    from src.core.agent import Agent

    assert get_current_kernel() is None
    agent = Agent(model="deepseek-v4-pro")
    assert isinstance(agent.messages, LoggedMessageList)
    assert agent.messages.verify() is True
    assert agent.get_tool_registry() is ToolRegistry.default()
