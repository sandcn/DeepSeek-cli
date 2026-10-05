"""内置工具插件化测试 — 每个工具是清单中的独立插件条目。

覆盖：
- 清单为每个内置工具声明独立条目（可 patch/overlay）；
- 默认 profile 经独立条目注册全部内置工具；
- overlay 禁用单个工具（tools::tool_x）真正生效（tools_builtin 不兜底重注册）；
- tools_builtin 在无清单接管时兜底注册；
- cordis 全局禁用集合可经策略插件 config 调整（is_globally_disabled）。
"""

from __future__ import annotations

import json

import pytest

from src.plugins.bootstrap import (
    build_kernel,
    inject_managed_config,
    resolve_entries,
    shutdown_kernel,
)
from src.plugins.manifest import build_config_tree
from src.kernel import Kernel, materialize


@pytest.fixture
async def cli_kernel():
    kernel = await build_kernel("cli")
    try:
        yield kernel
    finally:
        await shutdown_kernel(kernel)


def _resolved(profile="cli"):
    return materialize(resolve_entries(profile, discover_external=False))


def test_manifest_declares_each_builtin_tool():
    from src.tools.registry import discover_builtin_tools

    declared = set()
    ids = []
    for entry, plug in _resolved():
        if getattr(plug, "name", "") == "tool":
            declared.add((entry.config or {}).get("name"))
            ids.append(entry.id)
    assert set(discover_builtin_tools()) <= declared
    assert len(ids) == len(set(ids))
    assert all(i.startswith("tools::tool_") for i in ids)


def test_tree_declares_tools_and_commands_bundles():
    tree = build_config_tree()
    assert "tools" in tree.bundles()
    assert "commands" in tree.bundles()
    assert "tools" in tree.bundle("core").includes
    assert "commands" in tree.bundle("core").includes


async def test_default_profile_registers_all_builtin_tools(cli_kernel):
    from src.tools.registry import discover_builtin_tools

    names = set(cli_kernel.resolve_service("tools").names())
    assert set(discover_builtin_tools()) == names


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


async def test_overlay_disable_single_tool():
    kernel = await _build_with_disable(["tools::tool_mv", "tools::tool_rm"])
    try:
        names = set(kernel.resolve_service("tools").names())
        assert "mv" not in names
        assert "rm" not in names
        assert "read_file" in names
    finally:
        await kernel.dispose()


async def test_tools_builtin_fallback_when_not_managed():
    from src.plugins.config import apply as config_apply
    from src.plugins.tools import apply as tools_apply
    from src.plugins.tools_builtin import apply as tools_builtin_apply
    from src.tools.registry import discover_builtin_tools

    kernel = Kernel(name="t")
    kernel.mount(config_apply)
    kernel.mount(tools_apply)
    kernel.mount(tools_builtin_apply)
    await kernel.settle()
    try:
        names = set(kernel.resolve_service("tools").names())
        assert set(discover_builtin_tools()) <= names
    finally:
        await kernel.dispose()


def test_globally_disabled_default_contains_cordis():
    from src.tools.tool_policy import is_globally_disabled

    assert is_globally_disabled("cordis_inspect") is True
    assert is_globally_disabled("read_file") is False


async def test_globally_disabled_override_with_patch(tmp_path):
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
