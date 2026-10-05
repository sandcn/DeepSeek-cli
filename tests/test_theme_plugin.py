"""主题插件测试 — 每个内置主题一个清单条目。

覆盖：
- 清单为每个内置主题声明独立条目（name 一一对应）；
- 默认 profile 经独立条目注册全部内置主题；
- overlay 禁用单个主题真正生效（themes 聚合插件抑制默认装配）；
- 条目 config 的 palette 引用可替换实现；
- 直接 API：register/unregister/set_managed/disable 与 reset。
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

import src.tui.core._theme as th


def _fake_palette():
    return th.Palette(accent=th.Style(fg=99))


@pytest.fixture(autouse=True)
def _clean_theme():
    th.reset()
    yield
    th.reset()


@pytest.fixture
async def cli_kernel():
    kernel = await build_kernel("cli")
    try:
        yield kernel
    finally:
        await shutdown_kernel(kernel)


def _resolved(profile="cli"):
    return materialize(resolve_entries(profile, discover_external=False))


def test_manifest_declares_each_theme():
    themes = []
    for entry, plug in _resolved():
        if getattr(plug, "name", "") == "theme":
            themes.append((entry.id, (entry.config or {}).get("name")))
    assert [name for _, name in themes] == th.builtin_theme_names()
    assert all(entry_id.startswith("themes::theme_") for entry_id, _ in themes)


def test_tree_declares_themes_bundle():
    from src.plugins.manifest import build_config_tree

    tree = build_config_tree()
    assert "themes" in tree.bundles()
    assert "themes" in tree.bundle("presentation").includes


def test_manifest_declares_escape_monitor_entry():
    ids = [
        entry.id
        for entry, plug in _resolved()
        if getattr(plug, "name", "") == "escape_monitor"
    ]
    assert "core::escape_monitor" in ids


async def test_default_profile_themes(cli_kernel):
    service = cli_kernel.resolve_service("themes")
    assert service.names() == ["dark", "light", "high-contrast"]
    assert th.ThemeRegistry.names() == ("dark", "light", "high-contrast")


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


async def test_overlay_disable_theme():
    kernel = await _build_with_disable(["themes::theme_light"])
    try:
        assert "light" not in th.ThemeRegistry.names()
        assert "dark" in th.ThemeRegistry.names()
    finally:
        await kernel.dispose()


async def test_entry_replaces_theme():
    from src.plugins.config import apply as config_apply
    from src.plugins.theme_entries import apply_theme
    from src.plugins.themes import apply as themes_apply

    kernel = Kernel(name="t")
    kernel.mount(config_apply)
    kernel.mount(themes_apply)
    await kernel.settle()
    kernel.mount(apply_theme, config={"name": "light", "palette": "tests.test_theme_plugin._fake_palette"})
    await kernel.settle()
    try:
        palette = th.ThemeRegistry.get("light")
        assert palette is not None
        assert palette.accent.fg == 99
    finally:
        await kernel.dispose()


async def test_entry_without_name_fails():
    from src.plugins.config import apply as config_apply
    from src.plugins.theme_entries import apply_theme
    from src.plugins.themes import apply as themes_apply

    kernel = Kernel(name="t")
    kernel.mount(config_apply)
    kernel.mount(themes_apply)
    await kernel.settle()
    fiber = kernel.mount(apply_theme, config={})
    await kernel.settle()
    try:
        assert fiber.state.value == "FAILED"
        assert isinstance(fiber.error, ValueError)
    finally:
        await kernel.dispose()


def test_register_extension_theme():
    palette = th.Palette()
    undo = th.register_theme("mine", lambda: palette)
    try:
        assert th.ThemeRegistry.get("mine") is palette
        assert "mine" in th.ThemeRegistry.names()
    finally:
        undo()
    assert th.ThemeRegistry.get("mine") is None


def test_resolve_unknown_falls_back_dark():
    assert th.resolve_theme("nope") is th.ThemeRegistry.get("dark")


def test_disable_builtin_theme_roundtrip():
    undo = th.disable_builtin_themes(["light"])
    try:
        assert "light" not in th.ThemeRegistry.names()
    finally:
        undo()
    assert "light" in th.ThemeRegistry.names()
    with pytest.raises(KeyError):
        th.disable_builtin_themes(["nope"])


def test_managed_theme_roundtrip():
    undo = th.set_managed_builtin_themes(["light"])
    try:
        assert "light" not in th.ThemeRegistry.names()
        undo_reg = th.register_builtin_theme("light")
        try:
            assert "light" in th.ThemeRegistry.names()
        finally:
            undo_reg()
        assert "light" not in th.ThemeRegistry.names()
    finally:
        undo()
    assert "light" in th.ThemeRegistry.names()
