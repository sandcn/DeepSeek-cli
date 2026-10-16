"""内置命令插件化测试 — 每个命令是清单中的独立插件条目。

覆盖：
- 清单为每个内置命令声明独立条目；
- 声明 / 注册 API（declare_command_plugin / register_declared）；
- 默认 profile 经独立条目注册声明的命令；
- overlay 禁用单个命令（commands::cmd_x）真正生效；
- command_plugin 外部条目显式注册单个命令。
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


@pytest.fixture
async def cli_kernel():
    kernel = await build_kernel("cli")
    try:
        yield kernel
    finally:
        await shutdown_kernel(kernel)


def test_manifest_declares_each_builtin_command():
    ids = []
    names = set()
    for entry, plug in materialize(resolve_entries("cli", discover_external=False)):
        if getattr(plug, "name", "") == "command":
            ids.append(entry.id)
            names.add((entry.config or {}).get("name"))
    assert len(ids) == len(set(ids))
    assert all(i.startswith("commands::cmd_") for i in ids)
    assert {"clear", "plugin", "help", "model", "skill"} <= names


async def test_default_profile_registers_declared_commands(cli_kernel):
    from src.core.commands.base import get_plugin_registry

    registry = get_plugin_registry()
    assert registry is cli_kernel.resolve_service("commands").registry
    assert registry.get("plugin") is not None
    assert registry.count() >= 20


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


async def test_overlay_disable_single_command():
    kernel = await _build_with_disable(["commands::cmd_clear", "commands::cmd_plugin"])
    try:
        registry = kernel.resolve_service("commands").registry
        assert registry.get("clear") is None
        assert registry.get("plugin") is None
        assert registry.get("help") is not None
    finally:
        await kernel.dispose()


def test_declare_and_register_declared_api():
    from src.core.commands.base import (
        CommandPlugin,
        CommandMeta,
        command_registry_singleton,
        declare_command_plugin,
        declared_command_plugin,
        get_plugin_registry,
    )

    class _Tmp(CommandPlugin):
        def __init__(self):
            self.meta = CommandMeta(name="tmp_decl_test", description="t")

        def execute(self, ctx) -> bool:
            return True

    instance = _Tmp()
    declare_command_plugin(instance)
    assert declared_command_plugin("tmp_decl_test") is instance
    registry = command_registry_singleton()
    try:
        assert registry.register_declared("tmp_decl_test") is True
        assert registry.get("tmp_decl_test") is not None
        # 重复注册返回 False
        assert registry.register_declared("tmp_decl_test") is False
    finally:
        registry.unregister("tmp_decl_test")
        get_plugin_registry()


async def test_command_plugin_entry_registers_single_command(tmp_path):
    """自定义命令经 command_plugin 条目显式注册。"""
    from src.plugins.command_plugin import apply as command_entry_apply
    from src.plugins.commands import apply as commands_apply
    from src.plugins.config import apply as config_apply

    kernel = Kernel(name="t")
    kernel.mount(config_apply)
    kernel.mount(commands_apply, config={"managed_commands": []})
    await kernel.settle()
    try:
        kernel.mount(
            command_entry_apply,
            config={"command": "src.core.commands._plugin_cmd.PluginCommand", "name": "plugin"},
        )
        await kernel.settle()
        registry = kernel.resolve_service("commands").registry
        assert registry.get("plugin") is not None
    finally:
        await kernel.dispose()
