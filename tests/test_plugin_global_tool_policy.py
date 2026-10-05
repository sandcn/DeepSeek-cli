"""全局禁用工具条目化测试 — 每个禁用项是清单中的独立插件条目。

覆盖：
- 清单为每个内置禁用项声明独立条目（可 patch/overlay）；
- core bundle 引入 tool_policy bundle；
- 默认 profile 经独立条目禁用全部内置项；
- overlay 禁用单个条目 → 对应工具解除全局禁用；
- 注册表 API（接管 / 禁用 / 扩展 / 撤销 / 重置）；
- 策略插件 config 显式覆盖仍最高优先。
"""

from __future__ import annotations

import json

import pytest

from src.kernel import Kernel, materialize
from src.plugins.bootstrap import (
    build_kernel,
    inject_managed_config,
    resolve_entries,
    shutdown_kernel,
)
from src.plugins.manifest import GLOBAL_DISABLED_TOOL_ENTRIES, build_config_tree


def _resolved(profile="cli"):
    return materialize(resolve_entries(profile, discover_external=False))


def test_manifest_declares_each_global_disabled_tool():
    from src.tools.tool_policy import builtin_global_disabled_tool_ids

    names = []
    ids = []
    for entry, plug in _resolved():
        if getattr(plug, "name", "") == "global_disabled_tool":
            ids.append(entry.id)
            names.append((entry.config or {}).get("name"))
    assert set(names) == set(builtin_global_disabled_tool_ids())
    assert len(ids) == len(set(ids))
    assert all(i.startswith("tool_policy::global_disabled_tool_") for i in ids)


def test_manifest_entries_match_registry_declaration():
    from src.tools.tool_policy import GLOBAL_DISABLED_TOOLS, builtin_global_disabled_tool_ids

    assert set(builtin_global_disabled_tool_ids()) == set(GLOBAL_DISABLED_TOOLS)
    declared = [(e.get("config") or {}).get("name") for e in GLOBAL_DISABLED_TOOL_ENTRIES]
    assert set(declared) == set(GLOBAL_DISABLED_TOOLS)


def test_tree_declares_tool_policy_bundle():
    tree = build_config_tree()
    assert "tool_policy" in tree.bundles()
    assert "tool_policy" in tree.bundle("core").includes


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


async def test_overlay_disable_single_global_disabled_tool():
    from src.tools.tool_policy import is_globally_disabled

    kernel = await _build_with_disable(["tool_policy::global_disabled_tool_cordis_inspect"])
    try:
        assert is_globally_disabled("cordis_inspect") is False
        assert is_globally_disabled("cordis_run") is True
        assert is_globally_disabled("read_file") is False
    finally:
        await kernel.dispose()


async def test_default_profile_disables_all_builtin():
    from src.tools.tool_policy import builtin_global_disabled_tool_ids, is_globally_disabled

    kernel = await build_kernel("cli")
    try:
        for name in builtin_global_disabled_tool_ids():
            assert is_globally_disabled(name) is True
        assert is_globally_disabled("read_file") is False
    finally:
        await shutdown_kernel(kernel)


def test_registry_api_roundtrip():
    from src.tools import tool_policy as tp

    tp.reset()
    try:
        assert tp.active_global_disabled_tools() == set(tp.builtin_global_disabled_tool_ids())

        undo = tp.set_managed_builtin_global_disabled_tools(["cordis_run"])
        assert "cordis_run" not in tp.active_global_disabled_tools()
        undo()
        assert "cordis_run" in tp.active_global_disabled_tools()

        undo2 = tp.disable_builtin_global_disabled_tools(["cordis_stop"])
        assert "cordis_stop" not in tp.active_global_disabled_tools()
        undo2()
        assert "cordis_stop" in tp.active_global_disabled_tools()

        undo3 = tp.register_global_disabled_tool("my_custom_tool")
        assert "my_custom_tool" in tp.active_global_disabled_tools()
        assert tp.unregister_global_disabled_tool("my_custom_tool") is True
        assert "my_custom_tool" not in tp.active_global_disabled_tools()
        undo3()

        undo4 = tp.register_builtin_global_disabled_tool("cordis_define")
        assert "cordis_define" in tp.active_global_disabled_tools()
        undo4()
    finally:
        tp.reset()


async def test_policy_explicit_override_beats_entries(tmp_path):
    from src.tools.tool_policy import is_globally_disabled

    patch = {"replace": [{"id": "core::policy",
                          "config": {"globally_disabled_tools": ["read_file"]}}]}
    path = tmp_path / "patch.json"
    path.write_text(json.dumps(patch), encoding="utf-8")
    kernel = await build_kernel("cli", patch_paths=[str(path)])
    try:
        assert is_globally_disabled("read_file") is True
        assert is_globally_disabled("cordis_inspect") is False
    finally:
        await shutdown_kernel(kernel)
    assert is_globally_disabled("cordis_inspect") is True


@pytest.mark.parametrize("name", ["cordis_inspect", "cordis_run"])
async def test_disabled_entry_unmanages_without_override(name):
    from src.tools.tool_policy import is_globally_disabled

    kernel = await _build_with_disable([f"tool_policy::global_disabled_tool_{name}"])
    try:
        assert is_globally_disabled(name) is False
    finally:
        await kernel.dispose()
