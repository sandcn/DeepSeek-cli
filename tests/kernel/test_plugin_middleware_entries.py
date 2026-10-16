"""Agent 中间件独立插件条目测试 — 每个内置中间件一个清单条目。

覆盖：
- 清单为每个内置中间件声明独立条目（id 与内置 id 一一对应）；
- 默认 profile 经独立条目注册全部内置中间件（Agent 装配到 pipeline）；
- overlay 禁用单个中间件真正生效（agent_middleware 抑制默认装配）；
- 条目 config 的 middleware 引用可替换实现；
- 直接 API：register/unregister/set_managed/disable 与 reset。
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

import src.core.middleware.registry as reg
import src.renderer.extensions as ext


@pytest.fixture(autouse=True)
def _clean_registries():
    reg.reset()
    ext.reset()
    yield
    reg.reset()
    ext.reset()


@pytest.fixture
async def cli_kernel():
    kernel = await build_kernel("cli")
    try:
        yield kernel
    finally:
        await shutdown_kernel(kernel)


def _resolved(profile="cli"):
    return materialize(resolve_entries(profile, discover_external=False))


def _agent_middleware_names():
    from src.core.agent import Agent

    return [type(m).__name__ for m in Agent().pipeline.async_middlewares]


def test_manifest_declares_each_middleware():
    ids = []
    specs = []
    for entry, plug in _resolved():
        if getattr(plug, "name", "") == "middleware":
            ids.append(entry.id)
            specs.append((entry.config or {}).get("id"))
    assert specs == reg.builtin_middleware_ids()
    assert len(ids) == len(set(ids))
    assert all(i.startswith("middleware::middleware_") for i in ids)


def test_tree_declares_middleware_bundle():
    from src.plugins.manifest import build_config_tree

    tree = build_config_tree()
    assert "middleware" in tree.bundles()
    assert "middleware" in tree.bundle("runtime").includes


async def test_default_profile_registers_all_builtin(cli_kernel):
    assert len(reg.builtin_middleware_factories()) == 3
    names = _agent_middleware_names()
    assert "_InterruptCheckMiddleware" in names
    assert "_AsyncObservabilityMiddleware" in names
    assert "_AuditLogMiddleware" in names


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


async def test_overlay_disable_single_middleware():
    kernel = await _build_with_disable(["middleware::middleware_audit"])
    try:
        assert len(reg.builtin_middleware_factories()) == 2
        assert "_AuditLogMiddleware" not in _agent_middleware_names()
        assert "_InterruptCheckMiddleware" in _agent_middleware_names()
    finally:
        await kernel.dispose()
    assert len(reg.builtin_middleware_factories()) == 3


async def test_middleware_entry_replaces_implementation():
    from src.plugins.config import apply as config_apply
    from src.plugins.middleware_entries import apply_middleware

    kernel = Kernel(name="t")
    kernel.mount(config_apply)
    await kernel.settle()
    kernel.mount(
        apply_middleware,
        config={"id": "interrupt", "middleware": "src.core.middleware.audit._AuditLogMiddleware"},
    )
    await kernel.settle()
    try:
        names = [type(f()).__name__ for f in reg.builtin_middleware_factories()]
        assert names.count("_AuditLogMiddleware") == 2
        assert "_InterruptCheckMiddleware" not in names
    finally:
        await kernel.dispose()
    names = [type(f()).__name__ for f in reg.builtin_middleware_factories()]
    assert "_InterruptCheckMiddleware" in names


async def test_entry_without_id_fails():
    from src.plugins.config import apply as config_apply
    from src.plugins.middleware_entries import apply_middleware

    kernel = Kernel(name="t")
    kernel.mount(config_apply)
    await kernel.settle()
    fiber = kernel.mount(apply_middleware, config={})
    await kernel.settle()
    try:
        assert fiber.state.value == "FAILED"
        assert isinstance(fiber.error, ValueError)
    finally:
        await kernel.dispose()


def test_register_builtin_middleware_api_and_unknown():
    undo = reg.register_builtin_middleware("audit")
    try:
        assert len(reg.builtin_middleware_factories()) == 3
    finally:
        undo()
    with pytest.raises(KeyError):
        reg.register_builtin_middleware("nope")


def test_set_managed_and_register_roundtrip():
    undo_manage = reg.set_managed_builtin_middleware(["audit"])
    try:
        assert len(reg.builtin_middleware_factories()) == 2
        undo_reg = reg.register_builtin_middleware("audit")
        try:
            assert len(reg.builtin_middleware_factories()) == 3
        finally:
            undo_reg()
        assert len(reg.builtin_middleware_factories()) == 2
    finally:
        undo_manage()
    assert len(reg.builtin_middleware_factories()) == 3
    with pytest.raises(KeyError):
        reg.set_managed_builtin_middleware(["nope"])


def test_unregister_builtin_middleware():
    assert reg.unregister_builtin_middleware("audit") is False
    reg.register_builtin_middleware("audit")
    assert reg.unregister_builtin_middleware("audit") is True
