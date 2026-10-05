"""CLI 子命令条目化测试 — 每个顶层子命令是清单中的独立插件条目。

覆盖：
- 清单为每个内置子命令声明独立条目；
- core bundle 引入 subcommands bundle；
- 默认 profile 下 pre/post 阶段分派经注册表；
- overlay 禁用单个子命令条目真正生效；
- 条目 config 覆盖 phase/order/description；
- 注册表接管 / 禁用 / 扩展 API。
"""

from __future__ import annotations

import argparse

import pytest

from src.kernel import Kernel, materialize
from src.plugins.bootstrap import (
    build_kernel,
    inject_managed_config,
    resolve_entries,
    shutdown_kernel,
)
from src.plugins.manifest import SUBCOMMAND_ENTRIES, build_config_tree


def _resolved(profile="cli"):
    return materialize(resolve_entries(profile, discover_external=False))


def test_manifest_declares_each_subcommand():
    from src.app_init.subcommands import builtin_subcommand_ids

    names, ids = [], []
    for entry, plug in _resolved():
        if getattr(plug, "name", "") == "subcommand":
            names.append((entry.config or {}).get("name"))
            ids.append(entry.id)
    assert set(names) == set(builtin_subcommand_ids())
    assert len(ids) == len(set(ids))
    assert all(i.startswith("subcommands::subcommand_") for i in ids)


def test_manifest_entries_match_registry_declaration():
    from src.app_init.subcommands import builtin_subcommand_ids

    declared = {(e.get("config") or {}).get("name") for e in SUBCOMMAND_ENTRIES}
    assert declared == set(builtin_subcommand_ids())


def test_tree_declares_subcommands_bundle():
    tree = build_config_tree()
    assert "subcommands" in tree.bundles()
    assert "subcommands" in tree.bundle("core").includes


def test_phase_order_preserved_without_kernel():
    from src.app_init.subcommands import subcommands_for_phase

    pre = [c.name for c in subcommands_for_phase("pre")]
    post = [c.name for c in subcommands_for_phase("post")]
    assert pre == ["version", "dump-config", "plugin", "session", "config"]
    assert post == ["check-invariants", "clawbot"]


async def test_default_profile_dispatch_uses_registry():
    kernel = await build_kernel("cli")
    try:
        app = kernel.resolve_service("app")
        assert await app._run_subcommand_phase(argparse.Namespace(version=True, command=None), "pre") is True
        assert await app._run_subcommand_phase(
            argparse.Namespace(version=False, command="nonexistent"), "pre"
        ) is False
        assert await app._run_subcommand_phase(
            argparse.Namespace(version=False, command=None, check_invariants=True), "post"
        ) is True
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


async def test_overlay_disable_single_subcommand():
    kernel = await _build_with_disable(["subcommands::subcommand_version"])
    try:
        from src.app_init.subcommands import resolve_subcommand

        app = kernel.resolve_service("app")
        assert resolve_subcommand("version") is None
        assert await app._run_subcommand_phase(argparse.Namespace(version=True, command=None), "pre") is False
        assert resolve_subcommand("config") is not None
    finally:
        await kernel.dispose()


def test_registry_api_roundtrip():
    from src.app_init import subcommands as reg

    reg.reset()
    try:
        from src.app_init.subcommands import Subcommand

        assert set(reg.builtin_subcommand_ids()) == {
            "version", "dump-config", "plugin", "session", "config",
            "check-invariants", "clawbot",
        }
        undo = reg.set_managed_builtin_subcommands(["config"])
        assert reg.resolve_subcommand("config") is None
        undo()
        assert reg.resolve_subcommand("config") is not None

        undo2 = reg.disable_builtin_subcommands(["session"])
        assert reg.resolve_subcommand("session") is None
        undo2()

        async def _handler(app, args):
            return None

        undo3 = reg.register_subcommand(
            Subcommand("hello", "打招呼", phase="pre", order=99,
                       matcher=lambda args: getattr(args, "command", None) == "hello",
                       handler=_handler)
        )
        assert reg.resolve_subcommand("hello").name == "hello"
        assert "hello" in [c.name for c in reg.subcommands_for_phase("pre")]
        assert reg.unregister_subcommand("hello") is True
        undo3()
        assert reg.resolve_subcommand("hello") is None
    finally:
        reg.reset()


async def test_entry_config_override():
    from src.plugins.config import apply as config_apply
    from src.plugins.subcommand_entries import apply_subcommand

    kernel = Kernel(name="t")
    kernel.mount(config_apply)
    kernel.mount(apply_subcommand, config={
        "name": "version", "description": "自定义版本", "phase": "post", "order": 42,
    })
    await kernel.settle()
    try:
        from src.app_init.subcommands import resolve_subcommand

        cmd = resolve_subcommand("version")
        assert cmd.description == "自定义版本"
        assert cmd.phase == "post"
        assert cmd.order == 42
    finally:
        await kernel.dispose()
