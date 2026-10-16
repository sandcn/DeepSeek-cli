"""特殊键处理器条目化测试 — 每个内置 action 是清单中的独立插件条目。

覆盖：
- 清单为每个内置处理器声明独立条目；
- presentation bundle 引入 special_keys bundle；
- 默认 profile 下 make_special_key_callback 经注册表分发（vim/editmsg/retry/
  toggle_theme/switch_model/cycle_mode + empty_mode 兼容别名）；
- overlay 禁用单个处理器条目真正生效（该 action 返回 None）；
- 条目 config 覆盖处理器工厂；
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
from src.plugins.manifest import SPECIAL_KEY_ENTRIES, build_config_tree


def _resolved(profile="cli"):
    return materialize(resolve_entries(profile, discover_external=False))


def test_manifest_declares_each_special_key():
    from src.app_loop._special_handlers import builtin_special_key_ids

    ids = []
    entry_ids = []
    for entry, plug in _resolved():
        if getattr(plug, "name", "") == "special_key":
            ids.append((entry.config or {}).get("id"))
            entry_ids.append(entry.id)
    assert set(ids) == set(builtin_special_key_ids())
    assert all(i.startswith("special_keys::special_key_") for i in entry_ids)


def test_manifest_entries_match_registry_declaration():
    from src.app_loop._special_handlers import builtin_special_key_ids

    assert {(e.get("config") or {}).get("id") for e in SPECIAL_KEY_ENTRIES} == set(builtin_special_key_ids())


def test_tree_declares_special_keys_bundle():
    tree = build_config_tree()
    assert "special_keys" in tree.bundles()
    assert "special_keys" in tree.bundle("presentation").includes


class _FakeSession:
    def __init__(self):
        self.model = ""
        self._agent = None


class _FakeState:
    def __init__(self, model="m1"):
        self.model = model


class _FakeChatUI:
    def __init__(self):
        self.notifications = []

    def on_notification(self, msg):
        self.notifications.append(msg)

    @property
    def bottom_bar(self):
        return self

    def set_model_name(self, name):
        pass


async def test_default_profile_callback_dispatches():
    import src.app_loop._special_keys as sk

    kernel = await build_kernel("cli")
    try:
        cb = sk.make_special_key_callback(None, _FakeSession(), _FakeState(), _FakeChatUI())
        assert cb("editmsg", "t") == "/editmsg"
        assert cb("retry", "t") == "/retry"
        assert cb("unknown_action", "t") is None
        # empty_mode 兼容别名 → cycle_mode 处理器
        session = _FakeSession()
        cb2 = sk.make_special_key_callback(None, session, _FakeState(), _FakeChatUI())
        assert cb2("empty_mode", "t") == "t"
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


async def test_overlay_disable_single_handler():
    import src.app_loop._special_keys as sk

    kernel = await _build_with_disable(["special_keys::special_key_retry"])
    try:
        cb = sk.make_special_key_callback(None, _FakeSession(), _FakeState(), _FakeChatUI())
        assert cb("retry", "t") is None
        assert cb("editmsg", "t") == "/editmsg"
    finally:
        await kernel.dispose()


def test_registry_api_roundtrip():
    from src.app_loop import _special_handlers as reg
    from src.app_loop._special_keys import make_editmsg_handler

    reg.reset()
    try:
        undo = reg.set_managed_builtin_special_keys(["retry"])
        assert "retry" not in reg.active_special_key_factories()
        undo()
        assert "retry" in reg.active_special_key_factories()

        undo2 = reg.disable_builtin_special_keys(["editmsg"])
        assert "editmsg" not in reg.active_special_key_factories()
        undo2()
        assert "editmsg" in reg.active_special_key_factories()

        undo3 = reg.register_special_key("custom", make_editmsg_handler)
        assert reg.resolve_special_key_factory("custom") is make_editmsg_handler
        undo3()
        assert reg.resolve_special_key_factory("custom") is None
    finally:
        reg.reset()


async def test_entry_config_override():
    from src.plugins.config import apply as config_apply
    from src.plugins.special_key_entries import apply_special_key

    kernel = Kernel(name="t")
    kernel.mount(config_apply)
    kernel.mount(
        apply_special_key,
        config={"id": "editmsg", "handler": "src.app_loop._special_keys:make_retry_handler"},
    )
    await kernel.settle()
    try:
        import src.app_loop._special_keys as sk

        cb = sk.make_special_key_callback(None, _FakeSession(), _FakeState(), _FakeChatUI())
        assert cb("editmsg", "t") == "/retry"
    finally:
        await kernel.dispose()
