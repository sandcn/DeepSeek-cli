"""Preset 条目化测试 — 每个内置 preset 是清单中的独立插件条目。

覆盖：
- 清单为每个内置 preset 声明独立条目；
- core bundle 引入 presets bundle（且不再直接内联 presets 聚合条目）；
- 默认 profile 经独立条目注册全部内置 preset；
- overlay 禁用单个 preset 条目真正生效（聚合插件不兜底装配）；
- 条目 config 覆盖描述/模型等规格；
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
from src.plugins.manifest import PRESET_ENTRIES, build_config_tree


def _resolved(profile="cli"):
    return materialize(resolve_entries(profile, discover_external=False))


def test_manifest_declares_each_builtin_preset():
    from src.core.presets import builtin_preset_names

    names = []
    ids = []
    for entry, plug in _resolved():
        if getattr(plug, "name", "") == "preset":
            ids.append(entry.id)
            names.append((entry.config or {}).get("name"))
    assert set(names) == set(builtin_preset_names())
    assert len(ids) == len(set(ids))
    assert all(i.startswith("presets::preset_") for i in ids)


def test_manifest_entries_match_registry_declaration():
    from src.core.presets import builtin_preset_names

    declared = [(e.get("config") or {}).get("name") for e in PRESET_ENTRIES]
    assert set(declared) == set(builtin_preset_names())


def test_tree_declares_presets_bundle():
    tree = build_config_tree()
    assert "presets" in tree.bundles()
    assert "presets" in tree.bundle("core").includes
    # 聚合条目移入 presets bundle，core 不再内联（避免双实例）
    core_plugin_ids = [spec.id for spec in tree.bundle("core").plugins]
    assert "presets" not in core_plugin_ids


async def test_default_profile_registers_all_builtin_presets():
    kernel = await build_kernel("cli")
    try:
        from src.core.presets import builtin_preset_names

        presets = kernel.resolve_service("presets")
        assert set(presets.list()) == set(builtin_preset_names())
        assert presets.get("minimal").allows("bash") is False
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


async def test_overlay_disable_single_preset():
    kernel = await _build_with_disable(["presets::preset_minimal"])
    try:
        presets = kernel.resolve_service("presets")
        assert presets.list() == ["code", "standard"]
        with pytest.raises(KeyError):
            presets.get("minimal")
    finally:
        await kernel.dispose()


def test_registry_api_roundtrip():
    from src.core import presets as reg

    reg.reset()
    try:
        from src.core.presets import Preset

        assert set(reg.builtin_preset_names()) == {"standard", "minimal", "code"}
        undo = reg.set_managed_builtin_presets(["minimal"])
        assert "minimal" not in reg.active_presets()
        undo()
        assert "minimal" in reg.active_presets()

        undo2 = reg.disable_builtin_presets(["code"])
        assert "code" not in reg.active_presets()
        undo2()

        undo3 = reg.register_preset(Preset("readonly", "只读", tool_includes=("read_file",)))
        assert reg.resolve_preset("readonly").allows("read_file") is True
        assert reg.resolve_preset("readonly").allows("bash") is False
        assert reg.unregister_preset("readonly") is True
        undo3()
    finally:
        reg.reset()


async def test_entry_config_override_spec():
    from src.plugins.config import apply as config_apply
    from src.plugins.presets import apply as presets_apply
    from src.plugins.preset_entries import apply_preset

    kernel = Kernel(name="t")
    kernel.mount(config_apply)
    kernel.mount(presets_apply)
    kernel.mount(apply_preset, config={
        "name": "standard", "description": "自定义标准", "model": "my-model",
    })
    await kernel.settle()
    try:
        preset = kernel.resolve_service("presets").get("standard")
        assert preset.description == "自定义标准"
        assert preset.model == "my-model"
    finally:
        await kernel.dispose()
