"""内置插件与组合根测试 — 一切皆插件的端到端装配。"""

from __future__ import annotations

import asyncio

import pytest

from src.kernel import FiberState, Kernel, get_current_kernel, plugin
from src.plugins.bootstrap import build_kernel, dump_profile, shutdown_kernel


@pytest.fixture
async def cli_kernel():
    kernel = await build_kernel("cli")
    try:
        yield kernel
    finally:
        await shutdown_kernel(kernel)


def test_dump_profile_lists_all_plugins():
    text = dump_profile("cli")
    for key in ("core::config", "core::tools", "model::llm", "runtime::sessions",
                "runtime::agent_loop", "runtime::commands", "runtime::mcp",
                "presentation::ui", "presentation::renderer"):
        assert key in text


def test_dump_profile_unknown_raises():
    with pytest.raises(ValueError):
        dump_profile("does-not-exist")


async def test_cli_kernel_services(cli_kernel):
    expected = {
        "config", "events", "prompt", "policy", "tools", "skills", "llm",
        "sessions", "agent_loop", "commands", "mcp", "renderer", "ui",
    }
    assert expected <= set(cli_kernel.service_keys())


async def test_all_fibers_active(cli_kernel):
    for fiber in cli_kernel.fibers():
        assert fiber.state is FiberState.ACTIVE, fiber.name


async def test_tools_service_discovers_builtin(cli_kernel):
    tools = cli_kernel.resolve_service("tools")
    names = tools.names()
    assert "read_file" in names
    assert "bash" in names
    assert tools.schemas()


async def test_commands_service_registers_builtin(cli_kernel):
    commands = cli_kernel.resolve_service("commands")
    assert commands.count() > 0
    assert commands.get("help") is not None


async def test_policy_service(cli_kernel):
    policy = cli_kernel.resolve_service("policy")
    assert "write_file" in policy.excluded_tools("review")
    assert policy.can_use("read_file", "review")[0] is True


async def test_skills_service(cli_kernel):
    skills = cli_kernel.resolve_service("skills")
    assert isinstance(skills.enabled(), bool)
    assert isinstance(skills.list(), list)


async def test_prompt_service(cli_kernel):
    prompt = cli_kernel.resolve_service("prompt")
    parts = prompt.build()
    assert isinstance(parts, list)
    assert all(isinstance(p, str) for p in parts)


async def test_config_service(cli_kernel):
    config = cli_kernel.resolve_service("config")
    assert isinstance(config.rc(), dict)
    assert isinstance(config.providers(), dict)


async def test_llm_service(cli_kernel):
    llm = cli_kernel.resolve_service("llm")
    assert llm.default_model()
    assert llm.model_port() is not None


async def test_renderer_service(cli_kernel):
    renderer = cli_kernel.resolve_service("renderer")
    instance = renderer.create()
    assert instance is not None


async def test_ui_service(cli_kernel):
    ui = cli_kernel.resolve_service("ui")
    assert ui.theme_registry() is not None


async def test_agent_loop_creates_agent_using_kernel_registry(cli_kernel):
    loop = cli_kernel.resolve_service("agent_loop")
    agent = loop.create_agent(model="deepseek-v4-pro")
    assert agent.get_tool_registry() is cli_kernel.resolve_service("tools").registry


async def test_sessions_service_create(cli_kernel):
    sessions = cli_kernel.resolve_service("sessions")
    session = sessions.create()
    assert session.model


async def test_mcp_service_status(cli_kernel):
    mcp = cli_kernel.resolve_service("mcp")
    assert isinstance(mcp.status(), list)


async def test_minimal_profile_has_no_ui():
    kernel = await build_kernel("minimal")
    try:
        assert kernel.has_service("tools")
        assert not kernel.has_service("ui")
        assert not kernel.has_service("sessions")
    finally:
        await shutdown_kernel(kernel)


async def test_shutdown_clears_services_and_current():
    kernel = await build_kernel("cli")
    assert get_current_kernel() is kernel
    await shutdown_kernel(kernel)
    assert kernel.service_keys() == []
    assert get_current_kernel() is None


async def test_hot_mount_and_dispose_at_runtime(cli_kernel):
    @plugin("hot")
    def hot(ctx):
        ctx.provide("hot", "mounted")

    fiber = cli_kernel.mount(hot)
    await cli_kernel.settle()
    assert fiber.state is FiberState.ACTIVE
    assert cli_kernel.resolve_service("hot") == "mounted"

    await fiber.dispose()
    await cli_kernel.settle()
    assert not cli_kernel.has_service("hot")


async def test_external_plugin_dir_discovery(tmp_path):
    (tmp_path / "ext.py").write_text(
        "from src.kernel import plugin\n"
        "@plugin('external_demo')\n"
        "def apply(ctx):\n"
        "    ctx.provide('external_demo', 123)\n",
        encoding="utf-8",
    )
    kernel = await build_kernel("minimal", extra_dirs=[str(tmp_path)])
    try:
        assert kernel.resolve_service("external_demo") == 123
    finally:
        await shutdown_kernel(kernel)


async def test_kernel_runtime_bridge_uses_plugin_registry(cli_kernel):
    from src.core.adapters.kernel_runtime import active_tool_registry

    assert active_tool_registry() is cli_kernel.resolve_service("tools").registry


async def test_agent_default_registry_follows_kernel(cli_kernel):
    from src.core.agent import Agent

    agent = Agent(model="deepseek-v4-pro")
    assert agent.get_tool_registry() is cli_kernel.resolve_service("tools").registry


async def test_agent_falls_back_without_kernel():
    from src.core.agent import Agent
    from src.tools.registry import ToolRegistry

    assert get_current_kernel() is None
    agent = Agent(model="deepseek-v4-pro")
    assert agent.get_tool_registry() is ToolRegistry.default()
