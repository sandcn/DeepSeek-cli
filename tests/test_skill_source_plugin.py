"""技能来源插件测试 — 每个内置来源一个清单条目。

覆盖：
- 清单为每个内置来源声明独立条目（id 一一对应）；
- 默认 profile 经独立条目注册全部内置来源，``SkillRegistry.roots()`` 经来源解析；
- overlay 禁用单个来源真正生效（skill_sources 聚合插件抑制默认装配）；
- 条目 config 的 source 引用可替换实现；
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

import src.skills.source_registry as ssr


def _fake_source(registry, cwd=None):
    return [("fake-root", "fake", 999)]


@pytest.fixture(autouse=True)
def _clean_registry():
    ssr.reset()
    yield
    ssr.reset()


@pytest.fixture
async def cli_kernel():
    kernel = await build_kernel("cli")
    try:
        yield kernel
    finally:
        await shutdown_kernel(kernel)


def _resolved(profile="cli"):
    return materialize(resolve_entries(profile, discover_external=False))


def test_manifest_declares_each_source():
    sources = []
    for entry, plug in _resolved():
        if getattr(plug, "name", "") == "skill_source":
            sources.append((entry.id, (entry.config or {}).get("id")))
    assert [spec_id for _, spec_id in sources] == ssr.builtin_skill_source_ids()
    assert all(entry_id.startswith("skill_sources::skill_source_") for entry_id, _ in sources)


def test_tree_declares_skill_sources_bundle():
    from src.plugins.manifest import build_config_tree

    tree = build_config_tree()
    assert "skill_sources" in tree.bundles()
    assert "skill_sources" in tree.bundle("core").includes


async def test_default_profile_sources(cli_kernel):
    service = cli_kernel.resolve_service("skill_sources")
    assert set(service.source_names()) == {"project", "installed"}


async def test_registry_roots_use_source_registry(cli_kernel):
    from src.skills.registry import SkillRegistry

    registry = cli_kernel.resolve_service("skill_sources")
    roots = registry.resolve(SkillRegistry())
    assert any(source == "project" for _path, source, _rank in roots)


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


async def test_overlay_disable_source():
    kernel = await _build_with_disable(["skill_sources::skill_source_installed"])
    try:
        from src.skills.registry import SkillRegistry

        registry = SkillRegistry()
        roots = ssr.resolve_skill_sources(registry)
        assert all(source != "github" for _path, source, _rank in roots)
    finally:
        await kernel.dispose()


async def test_entry_replaces_source():
    from src.plugins.config import apply as config_apply
    from src.plugins.skill_source_entries import apply_skill_source
    from src.plugins.skill_sources import apply as skill_sources_apply

    kernel = Kernel(name="t")
    kernel.mount(config_apply)
    kernel.mount(skill_sources_apply)
    await kernel.settle()
    kernel.mount(
        apply_skill_source,
        config={"id": "project", "source": "tests.test_skill_source_plugin._fake_source"},
    )
    await kernel.settle()
    try:
        entries = ssr.resolve_skill_sources(object())
        assert entries == [("fake-root", "fake", 999)]
    finally:
        await kernel.dispose()


def test_resolve_without_kernel():
    from src.skills.registry import SkillRegistry

    entries = ssr.resolve_skill_sources(SkillRegistry())
    assert any(source == "project" for _path, source, _rank in entries)


def test_register_extension_source():
    undo = ssr.register_skill_source("mine", _fake_source)
    try:
        assert "mine" in ssr.skill_source_factories()
        entries = ssr.resolve_skill_sources(object())
        assert ("fake-root", "fake", 999) in entries
    finally:
        undo()
    assert ssr.skill_source_factories() == {}


def test_disable_builtin_source_roundtrip():
    undo = ssr.disable_builtin_skill_sources(["installed"])
    try:
        assert "installed" not in ssr.active_skill_source_factories()
    finally:
        undo()
    assert "installed" in ssr.active_skill_source_factories()
    with pytest.raises(KeyError):
        ssr.disable_builtin_skill_sources(["nope"])
