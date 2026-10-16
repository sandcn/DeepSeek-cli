"""MCP 插件独占管理器测试 — ``ctx.mcp.manager`` 为内核真源。

覆盖：
- ``McpManager.default()`` 内核挂载后返回 ``ctx.mcp`` 服务独占实例；
- ``ctx.mcp`` 的 setup/shutdown/status/prompt 全部委托该实例；
- 内核缺失时稳定回退进程级单例。
"""

from __future__ import annotations

import pytest

from src.plugins.bootstrap import build_kernel, shutdown_kernel


@pytest.fixture
async def headless_kernel():
    kernel = await build_kernel("headless")
    try:
        yield kernel
    finally:
        await shutdown_kernel(kernel)


async def test_mcp_default_is_kernel_service(headless_kernel):
    from src.mcp.manager import McpManager

    service = headless_kernel.resolve_service("mcp")
    assert service.manager is not None
    assert McpManager.default() is service.manager


async def test_mcp_default_survives_singleton_reset(headless_kernel):
    from src.mcp.manager import McpManager

    service = headless_kernel.resolve_service("mcp")
    McpManager.reset_default()
    assert McpManager.default() is service.manager


async def test_setup_mcp_uses_service_manager(headless_kernel, monkeypatch):
    from src.mcp.manager import McpManager

    service = headless_kernel.resolve_service("mcp")
    called = {}

    async def _fake_initialize(self, servers=None, registry=None, force=False):
        called["manager"] = self
        called["registry"] = registry
        return self

    monkeypatch.setattr(McpManager, "initialize", _fake_initialize)
    await service.setup_mcp()
    assert called["manager"] is service.manager
    assert called["registry"] is headless_kernel.resolve_service("tools").registry
    assert service.active is True


async def test_service_status_and_prompt_delegate(headless_kernel, monkeypatch):
    service = headless_kernel.resolve_service("mcp")
    monkeypatch.setattr(service.manager, "status", lambda: [{"name": "x"}])
    monkeypatch.setattr(service.manager, "build_prompt_section", lambda agent_type=None: "sec")
    assert service.status() == [{"name": "x"}]
    assert service.prompt_section("execute") == "sec"


def test_mcp_default_falls_back_without_kernel():
    from src.kernel import get_current_kernel, set_current_kernel
    from src.mcp.manager import McpManager

    set_current_kernel(None)
    assert get_current_kernel() is None
    first = McpManager.default()
    assert first is McpManager.default()
    McpManager.reset_default()
