"""MCP 管理器测试 — 连接编排 / 工具注册 / 权限策略 / 调用路由 / 关闭清理。"""

from __future__ import annotations

import asyncio

import pytest

from src.core.subagent import _TOOL_EXCLUSION_MAP
from src.mcp import manager as manager_mod
from src.mcp.client import McpToolDef, McpToolResult
from src.mcp.config import McpServerConfig
from src.mcp.manager import (
    McpManager,
    register_tool_policy,
    unregister_tool_policy,
)
from src.tools.registry import ToolRegistry


class FakeMcpClient:
    """可编程的假 MCP 客户端。"""

    instances: list = []

    def __init__(self, cfg):
        self.config = cfg
        self.name = cfg.name
        self.connected = False
        self.server_info = {"name": f"fake-{cfg.name}"}
        self.protocol_version = "2025-06-18"
        self.instructions = f"{cfg.name} 说明"
        self._fail = "fail" in cfg.name
        self.closed = False
        FakeMcpClient.instances.append(self)

    async def connect(self):
        if self._fail:
            raise RuntimeError("connect boom")
        self.connected = True

    async def list_tools(self):
        return [
            McpToolDef(name="echo", description="回显",
                       input_schema={"type": "object", "properties": {}}),
            McpToolDef(name="write thing", description="写入",
                       input_schema={"type": "object", "properties": {}}),
            # 与 "write thing" 清洗后同名（write_thing）→ 触发消歧哈希
            McpToolDef(name="write.thing", description="写入（碰撞）",
                       input_schema={"type": "object", "properties": {}}),
        ]

    async def call_tool(self, name, arguments=None):
        return McpToolResult(text=f"{self.name}:{name}:{sorted((arguments or {}).items())}")

    async def close(self):
        self.closed = True
        self.connected = False


@pytest.fixture(autouse=True)
def _isolate():
    snapshot = {k: set(v) for k, v in _TOOL_EXCLUSION_MAP.items()}
    FakeMcpClient.instances = []
    McpManager.reset_default()
    yield
    for key, value in snapshot.items():
        _TOOL_EXCLUSION_MAP[key] = value
    McpManager.reset_default()


def _registry():
    return ToolRegistry(initial_tools={})


def _servers():
    return [
        McpServerConfig(name="alpha", transport="stdio", command="x"),
        McpServerConfig(name="beta", transport="stdio", command="x", agents=["execute", "review"]),
        McpServerConfig(name="disabled", transport="stdio", command="x", enabled=False),
        McpServerConfig(name="fail", transport="stdio", command="x"),
    ]


def test_initialize_registers_tools_and_policies(monkeypatch):
    monkeypatch.setattr(manager_mod, "McpClient", FakeMcpClient)
    registry = _registry()
    mgr = McpManager(registry=registry)

    async def _run():
        await mgr.initialize(servers=_servers())
        return mgr

    asyncio.run(_run())
    tools = registry.get_tools()
    assert "mcp__alpha__echo" in tools
    assert "mcp__alpha__write_thing" in tools
    assert "mcp__beta__echo" in tools
    assert not any(k.startswith("mcp__disabled") for k in tools)
    assert not any(k.startswith("mcp__fail") for k in tools)

    # 权限：alpha 默认仅 execute；beta 额外允许 review
    assert "mcp__alpha__echo" in _TOOL_EXCLUSION_MAP["map"]
    assert "mcp__alpha__echo" in _TOOL_EXCLUSION_MAP["review"]
    assert "mcp__alpha__echo" in _TOOL_EXCLUSION_MAP["plan"]
    assert "mcp__alpha__echo" not in _TOOL_EXCLUSION_MAP["execute"]
    assert "mcp__beta__echo" not in _TOOL_EXCLUSION_MAP["review"]
    assert "mcp__beta__echo" in _TOOL_EXCLUSION_MAP["map"]

    status = {row["name"]: row for row in mgr.status()}
    assert status["alpha"]["connected"] is True
    assert status["disabled"]["enabled"] is False
    assert status["fail"]["connected"] is False
    assert "connect boom" in (status["fail"]["error"] or "")
    assert status["alpha"]["agents"] == ["execute"]


def test_initialize_is_idempotent(monkeypatch):
    monkeypatch.setattr(manager_mod, "McpClient", FakeMcpClient)
    mgr = McpManager(registry=_registry())

    async def _run():
        await mgr.initialize(servers=_servers())
        count_after_first = len(FakeMcpClient.instances)
        await mgr.initialize(servers=_servers())
        return count_after_first

    count_after_first = asyncio.run(_run())
    assert len(FakeMcpClient.instances) == count_after_first


def test_call_tool_routes_to_client(monkeypatch):
    monkeypatch.setattr(manager_mod, "McpClient", FakeMcpClient)
    mgr = McpManager(registry=_registry())

    async def _run():
        await mgr.initialize(servers=_servers())
        return await mgr.call_tool("alpha", "echo", {"a": 1})

    result = asyncio.run(_run())
    assert result.text == "alpha:echo:[('a', 1)]"


def test_call_tool_unknown_server_raises(monkeypatch):
    monkeypatch.setattr(manager_mod, "McpClient", FakeMcpClient)
    mgr = McpManager(registry=_registry())

    async def _run():
        await mgr.initialize(servers=_servers())
        with pytest.raises(Exception):
            await mgr.call_tool("nope", "echo", {})

    asyncio.run(_run())


def test_close_unregisters_and_cleans_policy(monkeypatch):
    monkeypatch.setattr(manager_mod, "McpClient", FakeMcpClient)
    registry = _registry()
    mgr = McpManager(registry=registry)

    async def _run():
        await mgr.initialize(servers=_servers())
        await mgr.close()

    asyncio.run(_run())
    assert registry.get_tools() == {}
    assert mgr.initialized is False
    for agent_type in ("map", "review", "plan", "execute"):
        assert not any(n.startswith("mcp__") for n in _TOOL_EXCLUSION_MAP[agent_type])
    assert all(client.closed for client in FakeMcpClient.instances if not client._fail)


def test_prompt_section_lists_servers(monkeypatch):
    monkeypatch.setattr(manager_mod, "McpClient", FakeMcpClient)
    mgr = McpManager(registry=_registry())

    async def _run():
        await mgr.initialize(servers=_servers())
        return mgr.build_prompt_section()

    section = asyncio.run(_run())
    assert "## MCP 外部工具" in section
    assert "`alpha`" in section
    assert "`mcp__alpha__echo`" in section
    assert "alpha 说明" in section


def test_prompt_section_empty_without_clients():
    assert McpManager(registry=_registry()).build_prompt_section() == ""


def test_prompt_section_filters_by_agent_type(monkeypatch):
    monkeypatch.setattr(manager_mod, "McpClient", FakeMcpClient)
    mgr = McpManager(registry=_registry())

    async def _run():
        await mgr.initialize(servers=_servers())
        return mgr.build_prompt_section("review"), mgr.build_prompt_section("execute")

    review_section, execute_section = asyncio.run(_run())
    # alpha 默认仅 execute → review 看不到
    assert "mcp__alpha__echo" not in review_section
    # beta 额外允许 review
    assert "mcp__beta__echo" in review_section
    assert "mcp__alpha__echo" in execute_section


def test_prompt_section_empty_when_all_filtered(monkeypatch):
    monkeypatch.setattr(manager_mod, "McpClient", FakeMcpClient)
    mgr = McpManager(registry=_registry())

    async def _run():
        await mgr.initialize(servers=[McpServerConfig(name="alpha", transport="stdio", command="x")])
        return mgr.build_prompt_section("map")

    assert asyncio.run(_run()) == ""


def test_force_reinitialize_closes_old_clients(monkeypatch):
    monkeypatch.setattr(manager_mod, "McpClient", FakeMcpClient)
    mgr = McpManager(registry=_registry())

    async def _run():
        await mgr.initialize(servers=[McpServerConfig(name="alpha", transport="stdio", command="x")])
        first = FakeMcpClient.instances[-1]
        await mgr.initialize(
            servers=[McpServerConfig(name="alpha", transport="stdio", command="x")],
            force=True,
        )
        return first

    first = asyncio.run(_run())
    assert first.closed is True


def test_tool_name_collision_disambiguated(monkeypatch):
    monkeypatch.setattr(manager_mod, "McpClient", FakeMcpClient)
    registry = _registry()
    mgr = McpManager(registry=registry)

    async def _run():
        await mgr.initialize(servers=[McpServerConfig(name="alpha", transport="stdio", command="x")])
        return sorted(n for n in registry.get_tools() if n.startswith("mcp__alpha__write_thing"))

    names = asyncio.run(_run())
    assert len(names) == 2  # 一个原名 + 一个哈希消歧，未静默覆盖


def test_close_clears_server_status(monkeypatch):
    monkeypatch.setattr(manager_mod, "McpClient", FakeMcpClient)
    mgr = McpManager(registry=_registry())

    async def _run():
        await mgr.initialize(servers=_servers())
        await mgr.close()
        return mgr.status()

    assert asyncio.run(_run()) == []


def test_register_and_unregister_tool_policy():
    names = ["mcp__x__y"]
    register_tool_policy(names, {"execute", "review"})
    assert names[0] not in _TOOL_EXCLUSION_MAP["execute"]
    assert names[0] not in _TOOL_EXCLUSION_MAP["review"]
    assert names[0] in _TOOL_EXCLUSION_MAP["map"]
    assert names[0] in _TOOL_EXCLUSION_MAP["plan"]

    unregister_tool_policy(names)
    for agent_type in ("map", "review", "plan", "execute"):
        assert names[0] not in _TOOL_EXCLUSION_MAP[agent_type]


def test_register_tool_policy_defaults_to_execute_only():
    names = ["mcp__z__w"]
    register_tool_policy(names)
    assert names[0] not in _TOOL_EXCLUSION_MAP["execute"]
    assert names[0] in _TOOL_EXCLUSION_MAP["review"]


def test_default_singleton_identity():
    assert McpManager.default() is McpManager.default()
    McpManager.reset_default()
    assert McpManager.default() is not None


def test_connected_tool_names(monkeypatch):
    monkeypatch.setattr(manager_mod, "McpClient", FakeMcpClient)
    mgr = McpManager(registry=_registry())

    async def _run():
        await mgr.initialize(servers=[McpServerConfig(name="alpha", transport="stdio", command="x")])
        return mgr.connected_tool_names()

    names = asyncio.run(_run())
    assert "mcp__alpha__echo" in names


def test_setup_and_shutdown_convenience(monkeypatch):
    monkeypatch.setattr(manager_mod, "McpClient", FakeMcpClient)
    registry = _registry()
    monkeypatch.setattr(ToolRegistry, "default", classmethod(lambda cls: registry))

    async def _run():
        await manager_mod.setup_mcp(servers=[McpServerConfig(name="alpha", transport="stdio", command="x")])
        assert "mcp__alpha__echo" in registry.get_tools()
        await manager_mod.shutdown_mcp()

    asyncio.run(_run())
    assert registry.get_tools() == {}
