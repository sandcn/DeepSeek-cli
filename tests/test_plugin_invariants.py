"""不变量条目化测试 — 每条内置检查一个独立插件条目。

覆盖：
- 清单为每条内置检查声明独立条目（可 patch/overlay）；
- invariants bundle 引入 core；
- 默认 profile 下 ctx.invariants 装配全部内置检查；
- overlay 禁用单条检查真正生效；
- 条目 config.check 替换检查实现；
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
from src.plugins.manifest import INVARIANT_ENTRIES, build_config_tree


def _resolved(profile="cli"):
    return materialize(resolve_entries(profile, discover_external=False))


def _always_fail_check(kernel):
    """测试用检查实现（经条目 config.check 点分引用替换内置检查）。"""
    return "自定义失败"


def test_manifest_declares_each_builtin_check():
    from src.kernel.invariant_registry import builtin_invariant_ids

    declared = []
    entry_ids = []
    for entry, plug in _resolved():
        if getattr(plug, "name", "") == "invariant":
            declared.append((entry.config or {}).get("name"))
            entry_ids.append(entry.id)
    assert set(declared) == set(builtin_invariant_ids())
    assert all(i.startswith("invariants::invariant_") for i in entry_ids)


def test_manifest_entries_match_registry_declaration():
    from src.kernel.invariant_registry import builtin_invariant_ids

    assert {(e.get("config") or {}).get("name") for e in INVARIANT_ENTRIES} == set(builtin_invariant_ids())


def test_tree_declares_invariants_bundle():
    tree = build_config_tree()
    assert "invariants" in tree.bundles()
    assert "invariants" in tree.bundle("core").includes


async def test_default_profile_checks():
    kernel = await build_kernel("cli")
    try:
        service = kernel.resolve_service("invariants")
        names = set(service.names())
        assert "services.keys_valid" in names
        assert "singletons.kernel_source" in names
        assert len(names) == len(service.managed())
        assert service.disabled() == []
        assert service.check() == []
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


async def test_overlay_disable_single_check():
    kernel = await _build_with_disable(["invariants::invariant_services_keys_valid"])
    try:
        service = kernel.resolve_service("invariants")
        names = set(service.names())
        assert "services.keys_valid" not in names
        assert "fibers.active_have_deps" in names
        assert len(names) == 41
    finally:
        await kernel.dispose()


async def test_overlay_replace_check_via_entry_config():
    from src.kernel.overlay import apply_overlay
    from src.kernel.invariant_registry import reset

    reset()
    try:
        entries = apply_overlay(
            resolve_entries("minimal", discover_external=False),
            {"replace": {"invariants::invariant_services_keys_valid": {
                "name": "services.keys_valid",
                "check": "tests.test_plugin_invariants:_always_fail_check",
            }}},
        )
        resolved = materialize(entries)
        inject_managed_config(resolved)
        kernel = Kernel(name="t", profile="minimal")
        for entry, plug in resolved:
            if entry.disabled:
                continue
            kernel.mount(plug, config=entry.config)
        await kernel.settle()
        try:
            service = kernel.resolve_service("invariants")
            failures = [f for f in service.check() if f.startswith("services.keys_valid")]
            assert failures and "自定义失败" in failures[0]
        finally:
            await kernel.dispose()
    finally:
        reset()


def test_registry_api_roundtrip():
    from src.kernel.invariant_registry import (
        active_checks,
        active_invariant_checks,
        disable_builtin_checks,
        managed_check_ids,
        register_check,
        reset,
        set_managed_builtin_checks,
    )

    reset()
    try:
        assert "services.keys_valid" in active_invariant_checks()
        undo = set_managed_builtin_checks(["services.keys_valid"])
        assert "services.keys_valid" not in active_invariant_checks()
        assert "services.keys_valid" in managed_check_ids()
        undo()
        assert "services.keys_valid" in active_invariant_checks()

        undo2 = disable_builtin_checks(["services.keys_valid"])
        assert "services.keys_valid" not in active_invariant_checks()
        undo2()
        assert "services.keys_valid" in active_invariant_checks()

        undo3 = register_check("custom.check", lambda kernel: None)
        assert "custom.check" in active_checks()
        undo3()
        assert "custom.check" not in active_checks()
    finally:
        reset()


@pytest.mark.parametrize("spec_id", ["services.keys_valid", "singletons.kernel_source"])
def test_default_spec_resolvable(spec_id):
    from src.kernel.invariant_registry import (
        active_invariant_checks,
        default_invariant_spec,
        resolve_check,
        reset,
    )

    reset()
    try:
        spec = default_invariant_spec(spec_id)
        assert spec.id == spec_id
        assert callable(resolve_check(spec))
        assert callable(active_invariant_checks()[spec_id])
    finally:
        reset()
