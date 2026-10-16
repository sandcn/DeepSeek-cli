"""工具表现条目化测试 — 每个工具/类别/Agent 类型是清单中的独立插件条目。

覆盖：
- 清单为每个内置表现声明独立条目；
- presentation bundle 引入 tool_styles bundle；
- 默认 profile 下工具类别/配色/Agent 缩写来自注册表；
- overlay 禁用单个工具/类别条目真正生效；
- 条目 config 覆盖类别/配色/缩写；
- 注册表接管 / 禁用 / 扩展 API；
- 消费方（toolcard / _subagent_render / _dispatcher）经注册表查询。
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
from src.plugins.manifest import TOOL_STYLE_ENTRIES, build_config_tree


def _resolved(profile="cli"):
    return materialize(resolve_entries(profile, discover_external=False))


def test_manifest_declares_each_presentation():
    from src.tui._tool_styles import builtin_presentation_ids

    ids = []
    entry_ids = []
    for entry, plug in _resolved():
        if getattr(plug, "name", "") == "tool_style":
            ids.append((entry.config or {}).get("id"))
            entry_ids.append(entry.id)
    assert set(ids) == set(builtin_presentation_ids())
    assert all(i.startswith("tool_styles::tool_style_") for i in entry_ids)


def test_manifest_entries_match_registry_declaration():
    from src.tui._tool_styles import builtin_presentation_ids

    assert {(e.get("config") or {}).get("id") for e in TOOL_STYLE_ENTRIES} == set(builtin_presentation_ids())


def test_tree_declares_tool_styles_bundle():
    tree = build_config_tree()
    assert "tool_styles" in tree.bundles()
    assert "tool_styles" in tree.bundle("presentation").includes


async def test_default_profile_presentations():
    from src.tui._tool_styles import (
        agent_type_abbrev,
        agent_type_style,
        category_style,
        tool_category,
        tool_icon,
        tool_style,
    )

    kernel = await build_kernel("cli")
    try:
        assert tool_category("bash") == "shell"
        assert tool_icon("bash") == "\u26a1"
        assert tool_style("read_file").fg == 81
        assert category_style("delete").fg == 203
        assert agent_type_abbrev("execute") == "ex"
        assert agent_type_style("plan").fg == 214
        # 未知工具 → 空/None
        assert tool_category("nope") == ""
        assert tool_style("nope") is None
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


async def test_overlay_disable_tool_presentation():
    from src.tui._tool_styles import tool_category

    kernel = await _build_with_disable(["tool_styles::tool_style_tool_bash"])
    try:
        assert tool_category("bash") == ""
        assert tool_category("read_file") == "file_read"
    finally:
        await kernel.dispose()


async def test_overlay_disable_category_style():
    from src.tui._tool_styles import tool_category, tool_style

    kernel = await _build_with_disable(["tool_styles::tool_style_cat_delete"])
    try:
        # 类别配色条目被禁用 → 类别仍在，但配色解析为 None
        assert tool_category("rm") == "delete"
        assert tool_style("rm") is None
    finally:
        await kernel.dispose()


def test_registry_api_roundtrip():
    from src.tui import _tool_styles as reg
    from src.tui._tool_styles import PresentationSpec

    reg.reset()
    try:
        undo = reg.set_managed_builtin_presentations(["tool_bash"])
        assert reg.tool_category("bash") == ""
        undo()
        assert reg.tool_category("bash") == "shell"

        undo2 = reg.disable_builtin_presentations(["cat_shell"])
        assert reg.category_style("shell") is None
        undo2()
        assert reg.category_style("shell").fg == 41

        undo3 = reg.register_presentation(
            PresentationSpec(id="tool_custom", kind="tool", name="custom", category="shell")
        )
        assert reg.tool_category("custom") == "shell"
        undo3()
        assert reg.tool_category("custom") == ""
    finally:
        reg.reset()


async def test_entry_config_override():
    from src.plugins.config import apply as config_apply
    from src.plugins.tool_style_entries import apply_tool_style

    kernel = Kernel(name="t")
    kernel.mount(config_apply)
    kernel.mount(apply_tool_style, config={"id": "tool_bash", "category": "search"})
    kernel.mount(apply_tool_style, config={"id": "cat_search", "fg": 100})
    await kernel.settle()
    try:
        from src.tui._tool_styles import category_style, tool_category, tool_style

        assert tool_category("bash") == "search"
        assert tool_style("bash").fg == 100
        assert category_style("search").fg == 100
    finally:
        await kernel.dispose()


async def test_consumers_use_registry():
    from src.plugins.config import apply as config_apply
    from src.plugins.tool_style_entries import apply_tool_style

    kernel = Kernel(name="t")
    kernel.mount(config_apply)
    kernel.mount(apply_tool_style, config={"id": "tool_bash", "category": "delete"})
    await kernel.settle()
    try:
        from src.tui.app.toolcard import _category_style
        from src.tui._subagent_render import _get_tool_color

        assert _category_style("bash").fg == 203
        assert _get_tool_color("bash").fg == 203
    finally:
        await kernel.dispose()
