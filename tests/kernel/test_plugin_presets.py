"""Preset 插件测试 — 每会话能力组合（isolate 作用域）。"""

from __future__ import annotations

import pytest

from src.plugins.bootstrap import build_kernel, shutdown_kernel


@pytest.fixture
async def cli_kernel():
    kernel = await build_kernel("cli")
    try:
        yield kernel
    finally:
        await shutdown_kernel(kernel)


def test_builtin_presets_listed(cli_kernel):
    presets = cli_kernel.resolve_service("presets")
    assert {"standard", "minimal", "code"} <= set(presets.list())


def test_preset_allows_semantics(cli_kernel):
    presets = cli_kernel.resolve_service("presets")
    assert presets.allows("standard", "bash") is True
    assert presets.allows("minimal", "bash") is False
    assert presets.allows("code", "bash") is True
    assert presets.allows("code", "web_search") is False


def test_preset_scope_is_isolated(cli_kernel):
    presets = cli_kernel.resolve_service("presets")
    scope = presets.scope("minimal")
    assert scope.preset.name == "minimal"
    # 隔离作用域解析到 preset；根上下文无 preset 服务
    assert cli_kernel.root.has("preset") is False
    assert presets.resolve_scoped("code").name == "code"


def test_filter_schemas(cli_kernel):
    presets = cli_kernel.resolve_service("presets")
    schemas = [
        {"type": "function", "function": {"name": "bash"}},
        {"type": "function", "function": {"name": "read_file"}},
        {"type": "function", "function": {"name": "web_search"}},
    ]
    names = [s["function"]["name"] for s in presets.filter_schemas("code", schemas)]
    assert names == ["bash", "read_file"]


def test_apply_to_agent(cli_kernel):
    presets = cli_kernel.resolve_service("presets")
    agent = cli_kernel.root.agent_loop.make_event_agent()
    presets.apply_to_agent("minimal", agent)
    assert agent.preset == "minimal"
    names = {schema["function"]["name"] for schema in agent.tools}
    assert "bash" not in names
    assert "read_file" in names


def test_create_agent_with_preset(cli_kernel):
    agent = cli_kernel.root.agent_loop.create_agent(preset="code")
    assert agent.preset == "code"
    names = {schema["function"]["name"] for schema in agent.tools}
    assert "bash" in names
    assert "web_search" not in names


def test_register_custom_preset(cli_kernel):
    from src.plugins.presets import Preset

    presets = cli_kernel.resolve_service("presets")
    presets.register(Preset("readonly", "只读", tool_includes=("read_file", "search")))
    assert presets.allows("readonly", "read_file") is True
    assert presets.allows("readonly", "bash") is False
    assert presets.unregister("readonly") is True
