"""可替换 Provider 细粒度插件化测试 — 通知后端 / 压缩策略 / MCP 传输。

覆盖：
- 清单为每类内置项声明独立条目（id 一一对应）；
- 默认 profile 经独立条目注册全部内置项；
- overlay 禁用单项真正生效（聚合插件抑制默认装配）；
- 条目 config 可替换实现；
- 通知模块按生效后端扇出发送；
- 直接 API：register/set_managed/disable 与 reset。
"""

from __future__ import annotations

import pytest

import src.mcp.transport_registry as mreg
import src.notifications.registry as nreg
from src.kernel import Kernel, materialize
from src.plugins.bootstrap import (
    build_kernel,
    inject_managed_config,
    resolve_entries,
    shutdown_kernel,
)


@pytest.fixture(autouse=True)
def _clean_registries():
    nreg.reset()
    mreg.reset()
    from src.plugins.context import ContextService

    yield
    nreg.reset()
    mreg.reset()


@pytest.fixture
async def cli_kernel():
    kernel = await build_kernel("cli")
    try:
        yield kernel
    finally:
        await shutdown_kernel(kernel)


def _resolved(profile="cli"):
    return materialize(resolve_entries(profile, discover_external=False))


async def _build_with_disable(ids):
    from src.kernel.overlay import apply_overlay

    entries = apply_overlay(resolve_entries("cli", discover_external=False), {"disable": list(ids)})
    resolved = materialize(entries)
    inject_managed_config(resolved)
    kernel = Kernel(name="t", profile="cli")
    for entry, plug in resolved:
        if entry.disabled:
            continue
        kernel.mount(plug, config=entry.config)
    await kernel.settle()
    return kernel


def test_manifest_declares_notification_backends():
    names = [e.config.get("id") for e, p in _resolved() if getattr(p, "name", "") == "notification_backend"]
    assert names == nreg.builtin_notification_backend_ids()
    ids = [e.id for e, p in _resolved() if getattr(p, "name", "") == "notification_backend"]
    assert all(i.startswith("notification_backends::notification_backend_") for i in ids)


def test_manifest_declares_mcp_transports():
    names = [e.config.get("name") for e, p in _resolved() if getattr(p, "name", "") == "mcp_transport"]
    assert names == mreg.builtin_mcp_transport_ids()
    ids = [e.id for e, p in _resolved() if getattr(p, "name", "") == "mcp_transport"]
    assert all(i.startswith("mcp_transports::mcp_transport_") for i in ids)


def test_manifest_declares_context_strategies():
    names = [e.config.get("name") for e, p in _resolved() if getattr(p, "name", "") == "context_strategy"]
    assert names == ["summarize", "drop"]
    from src.plugins.manifest import build_config_tree

    tree = build_config_tree()
    assert "context_strategies" in tree.bundle("core").includes
    assert "notification_backends" in tree.bundle("core").includes
    assert "mcp_transports" in tree.bundle("runtime").includes


async def test_default_profile_registers_all(cli_kernel):
    assert set(cli_kernel.resolve_service("notifications").backends()) == {"termux", "linux", "windows"}
    assert set(cli_kernel.resolve_service("context").strategy_names()) == {"summarize", "drop"}
    assert set(cli_kernel.resolve_service("mcp").transports()) == {"stdio", "http", "sse"}


async def test_overlay_disable_notification_backend():
    kernel = await _build_with_disable(["notification_backends::notification_backend_linux"])
    try:
        assert "linux" not in kernel.resolve_service("notifications").backends()
        assert "termux" in kernel.resolve_service("notifications").backends()
    finally:
        await kernel.dispose()
    assert "linux" in nreg.builtin_notification_backend_factories()


async def test_overlay_disable_context_strategy():
    kernel = await _build_with_disable(["context_strategies::context_strategy_drop"])
    try:
        names = kernel.resolve_service("context").strategy_names()
        assert "drop" not in names
        assert "summarize" in names
        # 默认策略链中缺失项被跳过，不中断构建
        strategies = kernel.resolve_service("context").build_strategies()
        assert len(strategies) == 1
    finally:
        await kernel.dispose()
    assert "drop" in await _strategies_after_cli()


async def _strategies_after_cli():
    kernel = await build_kernel("cli")
    try:
        return list(kernel.resolve_service("context").strategy_names())
    finally:
        await shutdown_kernel(kernel)


async def test_overlay_disable_mcp_transport():
    kernel = await _build_with_disable(["mcp_transports::mcp_transport_sse"])
    try:
        assert "sse" not in kernel.resolve_service("mcp").transports()
        assert "stdio" in kernel.resolve_service("mcp").transports()
    finally:
        await kernel.dispose()
    assert "sse" in mreg.builtin_mcp_transport_factories()


async def test_notification_module_fans_out_to_backends(monkeypatch):
    import src.notifications as notif

    calls = []

    class _Backend:
        def send(self, preview, title):
            calls.append((preview, title))

    undo = nreg.register_notification_backend("test", _Backend)
    try:
        monkeypatch.setattr(notif, "_last_notify_time", 0.0)
        monkeypatch.setattr(notif, "get_last_user_message_preview", lambda messages: "预览")
        monkeypatch.setattr(notif, "get_rc", lambda: {"enable_notifications": True, "notify_on_chat_completion": True})
        # 关闭内置平台后端，仅留测试后端（避免真实发通知）
        undo_dis = nreg.disable_builtin_notification_backends(["termux", "linux", "windows"])
        try:
            notif.notify_chat_completed([{"role": "user"}], elapsed=1.0)
        finally:
            undo_dis()
        assert calls and calls[0][0] == "预览"
    finally:
        undo()


async def test_notification_custom_backend_entry():
    from src.plugins.notification_backends import apply_notification_backend

    kernel = Kernel(name="t")
    await kernel.settle()
    kernel.mount(
        apply_notification_backend,
        config={"id": "termux", "backend": "src.notifications.backends.LinuxBackend"},
    )
    await kernel.settle()
    try:
        inst = nreg.builtin_notification_backend_factories()["termux"]()
        from src.notifications.backends import LinuxBackend

        assert isinstance(inst, LinuxBackend)
    finally:
        await kernel.dispose()


def test_registry_direct_apis():
    assert len(nreg.builtin_notification_backend_factories()) == 3
    undo = nreg.disable_builtin_notification_backends(["windows"])
    try:
        assert "windows" not in nreg.builtin_notification_backend_factories()
    finally:
        undo()
    with pytest.raises(KeyError):
        nreg.disable_builtin_notification_backends(["nope"])

    assert len(mreg.builtin_mcp_transport_factories()) == 3
    undo_m = mreg.disable_builtin_mcp_transports(["http"])
    try:
        assert "http" not in mreg.builtin_mcp_transport_factories()
    finally:
        undo_m()
    with pytest.raises(KeyError):
        mreg.disable_builtin_mcp_transports(["nope"])


def test_mcp_create_transport_uses_registry():
    from src.mcp.config import McpServerConfig
    from src.mcp.transport import StdioTransport, HttpTransport, SseTransport, create_transport

    stdio_cfg = McpServerConfig(name="s", transport="stdio", command="echo", args=[])
    assert isinstance(create_transport(stdio_cfg), StdioTransport)
    http_cfg = McpServerConfig(name="h", transport="http", url="https://x")
    assert isinstance(create_transport(http_cfg), HttpTransport)
    sse_cfg = McpServerConfig(name="e", transport="sse", url="https://x")
    assert isinstance(create_transport(sse_cfg), SseTransport)

    # 替换 stdio 实现 → create_transport 返回自定义实例
    undo = mreg.register_builtin_mcp_transport("stdio", lambda cfg: ("custom", cfg.name))
    try:
        assert create_transport(stdio_cfg) == ("custom", "s")
    finally:
        undo()
    assert isinstance(create_transport(stdio_cfg), StdioTransport)
