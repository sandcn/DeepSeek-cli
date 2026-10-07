"""键位绑定条目化测试 — 每个内置 Ctrl 绑定是清单中的独立插件条目。

覆盖：
- 清单为每个内置绑定声明独立条目；
- presentation bundle 引入 keybindings bundle；
- 默认 profile 下绑定解析来自注册表（与旧硬编码分支等价）；
- overlay 禁用单个绑定条目真正生效（该键 no-op）；
- 条目 config 覆盖 key/action/description；
- 注册表接管 / 禁用 / 扩展 API；
- InputDispatcher._handle_ctrl_key 经注册表分发。
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
from src.plugins.manifest import KEYBINDING_ENTRIES, build_config_tree


def _resolved(profile="cli"):
    return materialize(resolve_entries(profile, discover_external=False))


def test_manifest_declares_each_keybinding():
    from src.tui._keybindings import builtin_keybinding_ids

    ids = []
    entry_ids = []
    for entry, plug in _resolved():
        if getattr(plug, "name", "") == "keybinding":
            ids.append((entry.config or {}).get("id"))
            entry_ids.append(entry.id)
    assert set(ids) == set(builtin_keybinding_ids())
    assert all(i.startswith("keybindings::keybinding_") for i in entry_ids)


def test_manifest_entries_match_registry_declaration():
    from src.tui._keybindings import builtin_keybinding_ids

    assert {(e.get("config") or {}).get("id") for e in KEYBINDING_ENTRIES} == set(builtin_keybinding_ids())


def test_tree_declares_keybindings_bundle():
    tree = build_config_tree()
    assert "keybindings" in tree.bundles()
    assert "keybindings" in tree.bundle("presentation").includes


async def test_default_profile_resolves_bindings():
    from src.tui._keybindings import resolve_binding

    kernel = await build_kernel("cli")
    try:
        assert resolve_binding("\x07") == "vim"
        assert resolve_binding("\x0f") == "editmsg"
        assert resolve_binding("\x08") == "trace_toggle"
        assert resolve_binding("\x12") == "ctrl_r"
        assert resolve_binding("\x0c") == "clear_screen"
        assert resolve_binding("\x04") == "ctrl_d"
        assert resolve_binding("\x14") == "toggle_theme"
        assert resolve_binding("\x0e") == "switch_model"
        assert resolve_binding("\x10") == "history_prev"
        assert resolve_binding("\x02") == "cycle_mode"
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


async def test_overlay_disable_single_binding():
    from src.tui._keybindings import resolve_binding

    kernel = await _build_with_disable(["keybindings::keybinding_ctrl_g"])
    try:
        # Ctrl+G 的绑定条目被禁用 → 该键 no-op；其它键不受影响
        assert resolve_binding("\x07") is None
        assert resolve_binding("\x0f") == "editmsg"
    finally:
        await kernel.dispose()


def test_registry_api_roundtrip():
    from src.tui import _keybindings as reg
    from src.tui._keybindings import KeyBinding

    reg.reset()
    try:
        undo = reg.set_managed_builtin_keybindings(["ctrl_g"])
        assert reg.resolve_binding("\x07") is None
        undo()
        assert reg.resolve_binding("\x07") == "vim"

        undo2 = reg.disable_builtin_keybindings(["ctrl_o"])
        assert reg.resolve_binding("\x0f") is None
        undo2()
        assert reg.resolve_binding("\x0f") == "editmsg"

        undo3 = reg.register_keybinding(KeyBinding("custom", "\x01", "vim", "自定义"))
        assert reg.resolve_binding("\x01") == "vim"
        undo3()
        assert reg.resolve_binding("\x01") is None
    finally:
        reg.reset()


async def test_entry_config_override():
    from src.plugins.config import apply as config_apply
    from src.plugins.keybinding_entries import apply_keybinding

    kernel = Kernel(name="t")
    kernel.mount(config_apply)
    kernel.mount(apply_keybinding, config={"id": "ctrl_g", "key": "\x01", "action": "editmsg"})
    await kernel.settle()
    try:
        from src.tui._keybindings import resolve_binding

        assert resolve_binding("\x01") == "editmsg"
        assert resolve_binding("\x07") is None
    finally:
        await kernel.dispose()


def test_dispatcher_uses_registry(monkeypatch):
    from src.tui import _keybindings as reg
    from src.tui._input_dispatcher import InputDispatcher

    class _Stub(InputDispatcher):
        def __init__(self):
            self.calls = []
            self._reverse_search_enabled = False

        def _handle_special_key(self, action):
            self.calls.append(action)

    d = _Stub()
    reg.reset()
    try:
        InputDispatcher._handle_ctrl_key(d, "\x07")
        assert d.calls == ["vim"]
        # 未知组合键 no-op（\x11 Ctrl+Q 未绑定）
        InputDispatcher._handle_ctrl_key(d, "\x11")
        assert d.calls == ["vim"]
    finally:
        reg.reset()
