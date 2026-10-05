"""TUI 条目化测试（host / 补全提供者 / 状态栏段）。

覆盖：
- 清单为每个内置 host / 补全提供者 / 状态栏段声明独立条目；
- presentation bundle 引入对应 bundle；
- 默认 profile 下注册表生效；
- overlay 禁用单项真正生效；
- 条目 config 覆盖实现（点分引用）；
- 注册表接管 / 禁用 / 扩展 API。
"""

from __future__ import annotations

import pytest

from src.kernel import Kernel, materialize
from src.plugins.bootstrap import (
    build_kernel,
    inject_managed_config,
    resolve_entries,
    shutdown_kernel,
)
from src.plugins.manifest import (
    COMPLETION_PROVIDER_ENTRIES,
    HOST_ENTRIES,
    STATUS_SEGMENT_ENTRIES,
    build_config_tree,
)


def _resolved(profile="cli"):
    return materialize(resolve_entries(profile, discover_external=False))


# ── host ──────────────────────────────────────────────────


def test_manifest_declares_each_host():
    from src.tui.ink.registry import builtin_host_ids

    ids = []
    for entry, plug in _resolved():
        if getattr(plug, "name", "") == "host":
            ids.append((entry.config or {}).get("id"))
    assert set(ids) == set(builtin_host_ids())
    assert {(e.get("config") or {}).get("id") for e in HOST_ENTRIES} == set(builtin_host_ids())


def test_tree_declares_hosts_bundle():
    tree = build_config_tree()
    assert "hosts" in tree.bundles()
    assert "hosts" in tree.bundle("presentation").includes


async def test_default_host_active():
    from src.tui.ink.registry import get_host, has_host

    kernel = await build_kernel("cli")
    try:
        assert has_host("static-lines")
        assert get_host("static-lines") is not None
    finally:
        await shutdown_kernel(kernel)


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


async def test_overlay_disable_host():
    from src.tui.ink.registry import has_host

    kernel = await _build_with_disable(["hosts::host_static_lines"])
    try:
        assert not has_host("static-lines")
    finally:
        await kernel.dispose()


def test_host_registry_api_roundtrip():
    from src.tui.ink import registry as reg

    reg.reset()
    try:
        undo = reg.set_managed_builtin_hosts(["static-lines"])
        assert not reg.has_host("static-lines")
        undo()
        assert reg.has_host("static-lines")

        undo2 = reg.disable_builtin_hosts(["static-lines"])
        assert not reg.has_host("static-lines")
        undo2()
        assert reg.has_host("static-lines")
    finally:
        reg.reset()


def _fake_measure(fiber, avail_w):
    return (1, 1)


def _fake_paint(fiber, canvas):
    pass


async def test_host_entry_override():
    from src.plugins.config import apply as config_apply
    from src.plugins.host_entries import apply_host

    kernel = Kernel(name="t")
    kernel.mount(config_apply)
    kernel.mount(
        apply_host,
        config={
            "id": "static-lines",
            "measure": "tests.test_plugin_tui_entries:_fake_measure",
            "paint": "tests.test_plugin_tui_entries:_fake_paint",
        },
    )
    await kernel.settle()
    try:
        from src.tui.ink.registry import get_host

        assert get_host("static-lines") == (_fake_measure, _fake_paint)
    finally:
        await kernel.dispose()


# ── 补全提供者 ────────────────────────────────────────────


def test_manifest_declares_each_provider():
    from src.tui._completion_providers import builtin_provider_ids

    ids = []
    for entry, plug in _resolved():
        if getattr(plug, "name", "") == "completion_provider":
            ids.append((entry.config or {}).get("id"))
    assert set(ids) == set(builtin_provider_ids())
    assert {(e.get("config") or {}).get("id") for e in COMPLETION_PROVIDER_ENTRIES} == set(builtin_provider_ids())


def test_tree_declares_completion_providers_bundle():
    tree = build_config_tree()
    assert "completion_providers" in tree.bundles()
    assert "completion_providers" in tree.bundle("presentation").includes


async def test_default_completion_providers():
    from src.tui._completion_engine import CompletionEngine

    kernel = await build_kernel("cli")
    try:
        engine = CompletionEngine(commands_source=lambda: ["/help", "/model"])
        assert [i.text for i in engine.complete("/he")] == ["/help"]
    finally:
        await shutdown_kernel(kernel)


async def test_overlay_disable_command_provider():
    from src.tui._completion_engine import CompletionEngine

    kernel = await _build_with_disable(["completion_providers::completion_provider_command"])
    try:
        engine = CompletionEngine(commands_source=lambda: ["/help", "/model"])
        # 命令提供者缺席 → /he 无命令补全（param 无匹配 → 空）
        assert engine.complete("/he") == []
    finally:
        await kernel.dispose()


def test_completion_provider_registry_api_roundtrip():
    from src.tui import _completion_providers as reg

    reg.reset()
    try:
        undo = reg.set_managed_builtin_providers(["command"])
        assert "command" not in reg.active_provider_ids()
        undo()
        assert "command" in reg.active_provider_ids()

        undo2 = reg.disable_builtin_providers(["path"])
        assert "path" not in reg.active_provider_ids()
        undo2()
        assert "path" in reg.active_provider_ids()
    finally:
        reg.reset()


def _fake_provider(engine, ctx):
    from src.tui._completion_engine import CompletionItem

    return [CompletionItem("CUSTOM")]


async def test_completion_provider_entry_override():
    from src.plugins.config import apply as config_apply
    from src.plugins.completion_provider_entries import apply_completion_provider

    kernel = Kernel(name="t")
    kernel.mount(config_apply)
    kernel.mount(
        apply_completion_provider,
        config={"id": "command", "handler": "tests.test_plugin_tui_entries:_fake_provider"},
    )
    await kernel.settle()
    try:
        from src.tui._completion_engine import CompletionEngine

        engine = CompletionEngine(commands_source=lambda: ["/help"])
        assert [i.text for i in engine.complete("/he")] == ["CUSTOM"]
    finally:
        await kernel.dispose()


# ── 状态栏段 ──────────────────────────────────────────────


def test_manifest_declares_each_segment():
    from src.tui.app._status_segments import builtin_segment_ids

    ids = []
    for entry, plug in _resolved():
        if getattr(plug, "name", "") == "status_segment":
            ids.append((entry.config or {}).get("id"))
    assert set(ids) == set(builtin_segment_ids())
    assert {(e.get("config") or {}).get("id") for e in STATUS_SEGMENT_ENTRIES} == set(builtin_segment_ids())


def test_tree_declares_status_segments_bundle():
    tree = build_config_tree()
    assert "status_segments" in tree.bundles()
    assert "status_segments" in tree.bundle("presentation").includes


async def test_default_status_segments():
    from src.tui.app._status_segments import active_segment_ids

    kernel = await build_kernel("cli")
    try:
        assert set(active_segment_ids()) == {"model", "tools", "elapsed", "tokens", "speed"}
    finally:
        await shutdown_kernel(kernel)


async def test_overlay_disable_status_segment():
    from src.tui.app._status_segments import active_segment_ids

    kernel = await _build_with_disable(["status_segments::status_segment_tools"])
    try:
        assert "tools" not in active_segment_ids()
        assert "elapsed" in active_segment_ids()
    finally:
        await kernel.dispose()


def test_status_segment_registry_api_roundtrip():
    from src.tui.app import _status_segments as reg

    reg.reset()
    try:
        undo = reg.set_managed_builtin_segments(["tools"])
        assert "tools" not in reg.active_segment_ids()
        undo()
        assert "tools" in reg.active_segment_ids()

        undo2 = reg.disable_builtin_segments(["speed"])
        assert "speed" not in reg.active_segment_ids()
        undo2()
        assert "speed" in reg.active_segment_ids()
    finally:
        reg.reset()


def _fake_segment(ctx):
    from src.tui.ink import StyledRun

    return [StyledRun("X", None)]


async def test_status_segment_entry_override():
    from src.plugins.config import apply as config_apply
    from src.plugins.status_segment_entries import apply_status_segment

    kernel = Kernel(name="t")
    kernel.mount(config_apply)
    kernel.mount(
        apply_status_segment,
        config={"id": "tools", "handler": "tests.test_plugin_tui_entries:_fake_segment"},
    )
    await kernel.settle()
    try:
        from src.tui.app._status_segments import resolve_segment

        assert resolve_segment("tools") is _fake_segment
    finally:
        await kernel.dispose()
