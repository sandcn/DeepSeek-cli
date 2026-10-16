"""命名样式条目化测试 — 每个内置命名样式一条独立条目。

覆盖：
- 清单为每个内置命名样式声明独立条目（可 patch/overlay）；
- named_styles bundle 引入 presentation；
- 默认 profile 下 StyleSheet 查询来自注册表；
- overlay 禁用单条样式真正生效；
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
from src.plugins.manifest import NAMED_STYLE_ENTRIES, build_config_tree


def _resolved(profile="cli"):
    return materialize(resolve_entries(profile, discover_external=False))


def test_manifest_declares_each_builtin_style():
    from src.tui.core.style import builtin_style_names

    declared = []
    entry_ids = []
    for entry, plug in _resolved():
        if getattr(plug, "name", "") == "named_style":
            declared.append((entry.config or {}).get("name"))
            entry_ids.append(entry.id)
    assert set(declared) == set(builtin_style_names())
    assert all(i.startswith("named_styles::named_style_") for i in entry_ids)


def test_manifest_entries_match_registry_declaration():
    from src.tui.core.style import builtin_style_names

    assert {(e.get("config") or {}).get("name") for e in NAMED_STYLE_ENTRIES} == set(builtin_style_names())


def test_tree_declares_named_styles_bundle():
    tree = build_config_tree()
    assert "named_styles" in tree.bundles()
    assert "named_styles" in tree.bundle("presentation").includes


async def test_default_profile_styles():
    from src.tui.core.style import StyleSheet

    kernel = await build_kernel("cli")
    try:
        assert StyleSheet.get("error") is not None
        assert StyleSheet.get("diff_add") is not None
        assert StyleSheet.has("dim") is True
        assert "tree_leaf" in StyleSheet.all_names()
        assert "no_such_style" not in StyleSheet.all_names()
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


async def test_overlay_disable_single_style():
    from src.tui.core.style import StyleSheet

    kernel = await _build_with_disable(["named_styles::named_style_error"])
    try:
        assert StyleSheet.get("error") is None
        assert StyleSheet.has("error") is False
        assert StyleSheet.get("warn") is not None
    finally:
        await kernel.dispose()


def test_registry_api_roundtrip():
    from src.tui.core.style import (
        Style,
        active_style,
        disable_builtin_styles,
        register_style_extension,
        reset_style_registry,
        set_managed_builtin_styles,
    )

    reset_style_registry()
    try:
        assert active_style("error") is not None
        undo = set_managed_builtin_styles(["error"])
        assert active_style("error") is None
        undo()
        assert active_style("error") is not None

        undo2 = disable_builtin_styles(["error"])
        assert active_style("error") is None
        undo2()
        assert active_style("error") is not None

        undo3 = register_style_extension("custom_style", Style(fg=1))
        assert active_style("custom_style") is not None
        undo3()
        assert active_style("custom_style") is None
    finally:
        reset_style_registry()
