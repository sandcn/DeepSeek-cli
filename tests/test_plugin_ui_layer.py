"""UI 层插件化测试 — 事件消费者与 TUI 视图独立插件条目。

覆盖：
- 清单为每个内置消费者/视图声明独立条目；
- 默认 profile 经独立条目注册全部内置项；
- overlay 禁用单项真正生效；
- ``app.FULLSCREEN_VIEWS`` / ``BOTTOM_VIEWS`` 与视图注册表同源；
- 条目 config 可替换实现；
- 直接 API：register/set_managed/disable 与 reset。
"""

from __future__ import annotations

import pytest

import src.tui.events.consumer_registry as creg
import src.tui.app.view_registry as vreg
from src.kernel import Kernel, materialize
from src.plugins.bootstrap import (
    build_kernel,
    inject_managed_config,
    resolve_entries,
    shutdown_kernel,
)


@pytest.fixture(autouse=True)
def _clean_registries():
    creg.reset()
    vreg.reset()
    yield
    creg.reset()
    vreg.reset()


@pytest.fixture
async def cli_kernel():
    kernel = await build_kernel("cli")
    try:
        yield kernel
    finally:
        await shutdown_kernel(kernel)


def _resolved(profile="cli"):
    return materialize(resolve_entries(profile, discover_external=False))


def test_manifest_declares_consumers():
    specs = [e.config.get("id") for e, p in _resolved() if getattr(p, "name", "") == "consumer"]
    assert specs == creg.builtin_consumer_ids()
    ids = [e.id for e, p in _resolved() if getattr(p, "name", "") == "consumer"]
    assert all(i.startswith("consumers::consumer_") for i in ids)


def test_manifest_declares_ui_views():
    specs = [e.config.get("id") for e, p in _resolved() if getattr(p, "name", "") == "ui_view"]
    assert specs == vreg.builtin_view_ids()
    ids = [e.id for e, p in _resolved() if getattr(p, "name", "") == "ui_view"]
    assert all(i.startswith("ui_views::ui_view_") for i in ids)


def test_tree_bundles():
    from src.plugins.manifest import build_config_tree

    tree = build_config_tree()
    assert "consumers" in tree.bundle("presentation").includes
    assert "ui_views" in tree.bundle("presentation").includes


async def test_default_profile_registers_all(cli_kernel):
    assert set(cli_kernel.resolve_service("consumers").consumers()) == {"output", "chat_ui", "error_handler"}
    assert set(cli_kernel.resolve_service("ui").views()) == {
        "trace", "trace_tools", "config", "plugin", "help", "model",
        "user_select", "editmsg",
        # 2026-10 新增全屏视图批次
        "sessions", "changes", "theme", "skill", "mcp", "usage",
        "search", "outline", "keymap", "notify", "export",
        # 2026-10 会话日志 / 投影浏览器
        "logs",
    }


def test_app_views_share_registry_objects():
    from src.tui.app.app import FULLSCREEN_VIEWS, BOTTOM_VIEWS

    assert FULLSCREEN_VIEWS is vreg.fullscreen_views()
    assert BOTTOM_VIEWS is vreg.bottom_views()
    assert "trace" in FULLSCREEN_VIEWS
    assert "user_select" in BOTTOM_VIEWS


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


async def test_overlay_disable_view_and_consumer():
    kernel = await _build_with_disable([
        "ui_views::ui_view_trace",
        "consumers::consumer_error_handler",
    ])
    try:
        assert "trace" not in kernel.resolve_service("ui").views()
        assert "config" in kernel.resolve_service("ui").views()
        assert "error_handler" not in kernel.resolve_service("consumers").consumers()
        assert "output" in kernel.resolve_service("consumers").consumers()
    finally:
        await kernel.dispose()
    assert "trace" in vreg.active_view_ids()
    assert "error_handler" in creg.consumer_names()


async def test_view_entry_overrides_component():
    from src.plugins.ui_views import apply_ui_view

    kernel = Kernel(name="t")
    kernel.mount(
        apply_ui_view,
        config={"id": "plugin", "component": "src.tui.app.config_view.ConfigView"},
    )
    await kernel.settle()
    try:
        from src.tui.app.config_view import ConfigView

        assert vreg.fullscreen_views()["plugin"] is ConfigView
    finally:
        await kernel.dispose()
    from src.tui.app.plugin_view import PluginView

    assert vreg.fullscreen_views()["plugin"] is PluginView


async def test_consumer_entry_overrides_class():
    from src.plugins.consumer_entries import apply_consumer

    class _Custom:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    undo_holder = {}

    def _factory(**kwargs):
        return _Custom(**kwargs)

    kernel = Kernel(name="t")
    await kernel.settle()
    undo_holder["undo"] = creg.register_builtin_consumer("output", _factory)
    try:
        assert isinstance(creg.build_consumer("output"), _Custom)
    finally:
        undo_holder["undo"]()
    assert not isinstance(creg.build_consumer("output"), _Custom)


def test_registry_direct_apis():
    assert len(creg.builtin_consumer_factories()) == 3
    undo = creg.disable_builtin_consumers(["chat_ui"])
    try:
        assert "chat_ui" not in creg.builtin_consumer_factories()
        assert creg.build_consumer("chat_ui") is None
    finally:
        undo()
    with pytest.raises(KeyError):
        creg.disable_builtin_consumers(["nope"])

    assert "trace" in vreg.fullscreen_views()
    undo2 = vreg.disable_builtin_views(["trace"])
    try:
        assert "trace" not in vreg.fullscreen_views()
    finally:
        undo2()
    assert "trace" in vreg.fullscreen_views()
    with pytest.raises(KeyError):
        vreg.disable_builtin_views(["nope"])
