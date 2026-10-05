"""会话投影条目插件测试 — 每个内置投影一个清单条目。

覆盖：
- 清单为内置投影（turnBoundary）声明独立条目；
- 默认 profile 经独立条目注册内置投影，折叠语义正确；
- overlay 禁用单个投影真正生效（session_projections 聚合插件抑制默认装配）。
"""

from __future__ import annotations

import pytest

from src.core.events.agent_types import SessionEventType
from src.core.session_log.builtin_projections import (
    builtin_projection_names,
    builtin_projection_spec,
)
from src.kernel import Kernel, materialize
from src.plugins.bootstrap import (
    build_kernel,
    inject_managed_config,
    resolve_entries,
    shutdown_kernel,
)


@pytest.fixture
async def cli_kernel():
    kernel = await build_kernel("cli")
    try:
        yield kernel
    finally:
        await shutdown_kernel(kernel)


def _resolved(profile="cli"):
    return materialize(resolve_entries(profile, discover_external=False))


def test_manifest_declares_projection():
    entries = [
        (entry.id, (entry.config or {}).get("name"))
        for entry, plug in _resolved()
        if getattr(plug, "name", "") == "session_projection"
    ]
    assert [name for _, name in entries] == builtin_projection_names()
    assert entries[0][0] == "core::session_projection_turn_boundary"


async def test_default_turn_boundary(cli_kernel):
    projections = cli_kernel.resolve_service("session_projections")
    assert projections.has("turnBoundary") is True
    log = cli_kernel.resolve_service("session_log").create()
    log.append(SessionEventType.TURN_START)
    log.append(SessionEventType.STEP_START)
    log.append(SessionEventType.TURN_END, interrupted=False)
    state = projections.state_of("turnBoundary", log.events())
    assert state == {"turn": 1, "open": False, "steps": 1, "interrupted": False}


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


async def test_overlay_disable_turn_boundary():
    kernel = await _build_with_disable(["core::session_projection_turn_boundary"])
    try:
        projections = kernel.resolve_service("session_projections")
        assert projections.has("turnBoundary") is False
        assert projections.names() == []
    finally:
        await kernel.dispose()


async def test_service_register_builtin_without_kernel():
    from src.plugins.session_projections import apply as projections_apply

    kernel = Kernel(name="t")
    kernel.mount(projections_apply)
    await kernel.settle()
    try:
        service = kernel.resolve_service("session_projections")
        assert service.has("turnBoundary") is True
        undo = service.register_builtin("turnBoundary")
        try:
            assert service.has("turnBoundary") is True
        finally:
            undo()
    finally:
        await kernel.dispose()


def test_builtin_projection_spec_unknown():
    with pytest.raises(KeyError):
        builtin_projection_spec("nope")
