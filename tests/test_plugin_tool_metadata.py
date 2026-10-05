"""工具元数据条目化测试 — 每个内置工具一条独立元数据条目。

覆盖：
- 清单为每个内置工具声明独立元数据条目（可 patch/overlay）；
- tool_metadata bundle 引入 core；
- 默认 profile 下元数据来自注册表（消费方 Func.get_metadata 生效）；
- overlay 禁用单条元数据真正生效（回退类装饰器/None）；
- 条目 config 覆盖单条元数据；
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
from src.plugins.manifest import TOOL_METADATA_ENTRIES, build_config_tree


def _resolved(profile="cli"):
    return materialize(resolve_entries(profile, discover_external=False))


def test_manifest_declares_each_builtin_metadata():
    from src.tools.metadata_registry import builtin_tool_names

    declared = []
    entry_ids = []
    for entry, plug in _resolved():
        if getattr(plug, "name", "") == "tool_metadata_entry":
            declared.append((entry.config or {}).get("name"))
            entry_ids.append(entry.id)
    assert set(declared) == set(builtin_tool_names())
    assert all(i.startswith("tool_metadata::tool_metadata_") for i in entry_ids)


def test_manifest_entries_match_registry_declaration():
    from src.tools.metadata_registry import builtin_tool_names

    assert {(e.get("config") or {}).get("name") for e in TOOL_METADATA_ENTRIES} == set(builtin_tool_names())


def test_tree_declares_tool_metadata_bundle():
    tree = build_config_tree()
    assert "tool_metadata" in tree.bundles()
    assert "tool_metadata" in tree.bundle("core").includes


async def test_default_profile_metadata():
    from src.tools.base import get_tool_metadata
    from src.tools.bash import BashFunc
    from src.tools.read_file import ReadFileFunc
    from src.tools.user_select import UserSelectFunc

    kernel = await build_kernel("cli")
    try:
        service = kernel.resolve_service("tool_metadata")
        assert set(service.names()) <= set(service.active())
        assert get_tool_metadata(ReadFileFunc).parallel_safe is True
        assert get_tool_metadata(ReadFileFunc).tool_category == "read"
        assert get_tool_metadata(BashFunc).priority == 30
        assert get_tool_metadata(UserSelectFunc).parallel_safe is True
        assert get_tool_metadata(UserSelectFunc).timeout_estimate == 120
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


async def test_overlay_disable_single_metadata():
    from src.tools.base import get_tool_metadata
    from src.tools.read_file import ReadFileFunc
    from src.tools.read_image import ReadImageFunc

    kernel = await _build_with_disable(["tool_metadata::tool_metadata_read_file"])
    try:
        assert get_tool_metadata(ReadFileFunc) is None
        assert get_tool_metadata(ReadImageFunc).tool_category == "read"
    finally:
        await kernel.dispose()


async def test_entry_config_override_metadata():
    from src.plugins.config import apply as config_apply
    from src.plugins.tool_metadata_entries import apply_tool_metadata
    from src.tools.base import get_tool_metadata
    from src.tools.read_file import ReadFileFunc

    kernel = Kernel(name="t")
    kernel.mount(config_apply, config={"managed_tool_metadata": ["read_file"]})
    kernel.mount(
        apply_tool_metadata,
        config={"name": "read_file", "metadata": {"tool_category": "write", "priority": 1}},
    )
    await kernel.settle()
    try:
        meta = get_tool_metadata(ReadFileFunc)
        assert meta.tool_category == "write"
        assert meta.priority == 1
        assert meta.parallel_safe is False
    finally:
        await kernel.dispose()


def test_registry_api_roundtrip():
    from src.tools.metadata_registry import (
        metadata_for,
        reset,
        register_metadata,
        set_managed_builtin_metadata,
        disable_builtin_metadata,
    )

    reset()
    try:
        assert metadata_for("read_file")["tool_category"] == "read"
        undo = set_managed_builtin_metadata(["read_file"])
        assert metadata_for("read_file") is None
        undo()
        assert metadata_for("read_file")["tool_category"] == "read"

        undo2 = disable_builtin_metadata(["read_file"])
        assert metadata_for("read_file") is None
        undo2()
        assert metadata_for("read_file") is not None

        undo3 = register_metadata("custom_tool", {"tool_category": "read"})
        assert metadata_for("custom_tool")["tool_category"] == "read"
        undo3()
        assert metadata_for("custom_tool") is None
    finally:
        reset()
