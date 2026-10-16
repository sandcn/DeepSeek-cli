"""内核集成测试 — 应用层组合经内核服务解析（一切皆插件）。

覆盖：
- 内置工具注册表与进程级默认注册表同源（MCP 动态工具可被调度器 dispatch）；
- 应用层 Agent / Session / ChatUI 工厂的内核优先解析；
- ChatSession 默认无 UI Agent 的内核优先解析；
- kernel_runtime 访问器在有/无内核时的行为。
"""

from __future__ import annotations

import pytest

from src.kernel import get_current_kernel
from src.plugins.bootstrap import build_kernel, shutdown_kernel


@pytest.fixture
async def cli_kernel():
    kernel = await build_kernel("cli")
    try:
        yield kernel
    finally:
        await shutdown_kernel(kernel)


async def test_tools_registry_shares_default_singleton(cli_kernel):
    """内核工具注册表 = 进程级默认注册表（调度器/MCP 同源）。"""
    from src.tools.registry import ToolRegistry

    assert cli_kernel.resolve_service("tools").registry is ToolRegistry.default()


async def test_event_agent_factory_uses_kernel(cli_kernel):
    from src.app_loop._agent_factory import _make_event_agent
    from src.core.agent import Agent

    agent = _make_event_agent()
    assert isinstance(agent, Agent)
    assert agent.get_tool_registry() is cli_kernel.resolve_service("tools").registry


async def test_create_session_event_agent_uses_kernel(cli_kernel):
    from src.app_loop._session_factory import create_session

    session = create_session(event_agent=True)
    assert session.agent.get_tool_registry() is cli_kernel.resolve_service("tools").registry


async def test_chat_session_default_agent_uses_kernel_headless(cli_kernel):
    from src.core.session import ChatSession
    from src.core.adapters.null import _NullDisplayPort, _NullEventPort, _NullOutputPort

    session = ChatSession()
    assert isinstance(session.agent.display, _NullDisplayPort)
    assert isinstance(session.agent._event_port, _NullEventPort)
    assert isinstance(session.agent._output_port, _NullOutputPort)


async def test_agent_loop_headless_agent_null_ports(cli_kernel):
    from src.core.adapters.null import _NullDisplayPort

    loop = cli_kernel.resolve_service("agent_loop")
    agent = loop.create_headless_agent()
    assert isinstance(agent.display, _NullDisplayPort)
    assert agent.get_tool_registry() is cli_kernel.resolve_service("tools").registry


async def test_application_session_manager_uses_kernel(cli_kernel):
    from src.application import AppContext, SessionManager

    mgr = SessionManager(AppContext())
    session = mgr.create_session()
    assert session.agent.get_tool_registry() is cli_kernel.resolve_service("tools").registry


async def test_chat_ui_factory_delegates_to_service(cli_kernel, monkeypatch):
    import src.tui.consumer as consumer_mod

    sentinel = object()
    monkeypatch.setattr(consumer_mod, "ChatUIConsumer", lambda: sentinel)

    from src.app_loop._ui_factory import create_chat_ui

    assert create_chat_ui() is sentinel


async def test_kernel_runtime_accessors(cli_kernel):
    from src.core.adapters import kernel_runtime as kr

    assert kr.has_kernel()
    assert kr.active_agent_factory() is not None
    assert kr.active_headless_agent_factory() is not None
    assert kr.active_session_factory() is not None
    assert kr.active_chat_ui_factory() is not None
    assert kr.active_skill_registry() is cli_kernel.resolve_service("skills").registry
    assert kr.active_command_registry() is cli_kernel.resolve_service("commands").registry


async def test_kernel_runtime_agent_factory_returns_event_agent(cli_kernel):
    from src.core.adapters import kernel_runtime as kr
    from src.core.adapters.display import DefaultDisplayAdapter

    factory = kr.active_agent_factory()
    agent = factory("deepseek-v4-pro")
    assert agent.model == "deepseek-v4-pro"
    assert isinstance(agent.display, DefaultDisplayAdapter)


def test_kernel_runtime_fallbacks_without_kernel():
    from src.core.adapters import kernel_runtime as kr
    from src.tools.registry import ToolRegistry

    assert get_current_kernel() is None
    assert not kr.has_kernel()
    assert kr.active_agent_factory() is None
    assert kr.active_headless_agent_factory() is None
    assert kr.active_session_factory() is None
    assert kr.active_chat_ui_factory() is None
    assert kr.active_tool_registry() is ToolRegistry.default()


def test_make_event_agent_fallback_without_kernel():
    from src.app_loop._agent_factory import _make_event_agent
    from src.core.agent import Agent
    from src.core.adapters.display import DefaultDisplayAdapter

    assert get_current_kernel() is None
    agent = _make_event_agent()
    assert isinstance(agent, Agent)
    assert isinstance(agent.display, DefaultDisplayAdapter)


def test_create_session_fallback_without_kernel():
    from src.app_loop._session_factory import create_session
    from src.core.session import ChatSession

    assert get_current_kernel() is None
    session = create_session(event_agent=True)
    assert isinstance(session, ChatSession)
    assert session.model


def test_chat_ui_factory_fallback_without_kernel(monkeypatch):
    import src.tui.consumer as consumer_mod

    sentinel = object()
    monkeypatch.setattr(consumer_mod, "ChatUIConsumer", lambda: sentinel)

    from src.app_loop._ui_factory import create_chat_ui

    assert get_current_kernel() is None
    assert create_chat_ui() is sentinel
