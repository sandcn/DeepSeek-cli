"""事件类型条目化测试 — 每个内置事件类型一条独立条目。

覆盖：
- 清单为每个内置事件类型声明独立条目（可 patch/overlay）；
- event_types bundle 引入 core；
- 默认 profile 下 ALL_EVENT_TYPES / 核心事件常量来自注册表；
- overlay 禁用单条事件类型真正生效；
- 条目 config 覆盖核心事件类型字符串；
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
from src.plugins.manifest import EVENT_TYPE_ENTRIES, build_config_tree


def _resolved(profile="cli"):
    return materialize(resolve_entries(profile, discover_external=False))


def test_manifest_declares_each_builtin_event_type():
    import src.core.events.display_types  # noqa: F401
    import src.core.events.event_types  # noqa: F401
    from src.core.events.type_registry import builtin_composite_ids

    declared = []
    entry_ids = []
    for entry, plug in _resolved():
        if getattr(plug, "name", "") == "event_type":
            declared.append((entry.config or {}).get("name"))
            entry_ids.append(entry.id)
    assert set(declared) == set(builtin_composite_ids())
    assert all(i.startswith("event_types::event_type_") for i in entry_ids)


def test_manifest_entries_match_registry_declaration():
    import src.core.events.display_types  # noqa: F401
    import src.core.events.event_types  # noqa: F401
    from src.core.events.type_registry import builtin_composite_ids

    assert {(e.get("config") or {}).get("name") for e in EVENT_TYPE_ENTRIES} == set(builtin_composite_ids())


def test_tree_declares_event_types_bundle():
    tree = build_config_tree()
    assert "event_types" in tree.bundles()
    assert "event_types" in tree.bundle("core").includes


async def test_default_profile_event_types():
    from src.core.events import display_types
    from src.core.events.type_registry import event_value

    kernel = await build_kernel("cli")
    try:
        assert display_types.ToolNoticeEvent in display_types.ALL_EVENT_TYPES
        assert event_value("core", "MODEL_CALL_STARTED") == "model.call.started"
        assert display_types.active_events("display")["ToolNoticeEvent"] is display_types.ToolNoticeEvent
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


async def test_overlay_disable_display_event_type():
    from src.core.events import display_types

    kernel = await _build_with_disable(
        ["event_types::event_type_display_toolnoticeevent"]
    )
    try:
        assert display_types.ToolNoticeEvent not in display_types.ALL_EVENT_TYPES
        assert display_types.ToolStartedEvent in display_types.ALL_EVENT_TYPES
    finally:
        await kernel.dispose()


async def test_overlay_disable_core_event_type():
    from src.core.events import event_types

    kernel = await _build_with_disable(
        ["event_types::event_type_core_model_call_started"]
    )
    try:
        with pytest.raises(AttributeError):
            _ = event_types.MODEL_CALL_STARTED
        assert event_types.MODEL_CALL_COMPLETED == "model.call.completed"
    finally:
        await kernel.dispose()


async def test_entry_config_override_core_event_type():
    from src.plugins.config import apply as config_apply
    from src.plugins.event_type_entries import apply_event_type
    from src.core.events.type_registry import event_value

    kernel = Kernel(name="t")
    kernel.mount(config_apply, config={"managed_event_types": ["core::CONFIG_CHANGED"]})
    kernel.mount(
        apply_event_type,
        config={"name": "core::CONFIG_CHANGED", "value": "config.updated"},
    )
    await kernel.settle()
    try:
        assert event_value("core", "CONFIG_CHANGED") == "config.updated"
    finally:
        await kernel.dispose()


def test_registry_api_roundtrip():
    import src.core.events.display_types  # noqa: F401
    import src.core.events.event_types  # noqa: F401
    from src.core.events.type_registry import (
        event_value,
        reset,
        register_event,
        registered_composite_ids,
        disable_builtin_events,
        set_managed_builtin_events,
    )

    reset()
    try:
        assert event_value("core", "CONFIG_CHANGED") == "config.changed"
        undo = set_managed_builtin_events(["core::CONFIG_CHANGED"])
        assert event_value("core", "CONFIG_CHANGED") is None
        undo()
        assert event_value("core", "CONFIG_CHANGED") == "config.changed"

        undo2 = disable_builtin_events(["core::CONFIG_CHANGED"])
        assert event_value("core", "CONFIG_CHANGED") is None
        undo2()
        assert event_value("core", "CONFIG_CHANGED") == "config.changed"

        undo3 = register_event("custom::Ping", object)
        assert "custom::Ping" in registered_composite_ids()
        undo3()
        assert "custom::Ping" not in registered_composite_ids()
    finally:
        reset()
