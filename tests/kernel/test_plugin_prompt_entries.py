"""提示词条目化测试 — 运行模式 / 提词来源是清单中的独立插件条目。

覆盖：
- 清单为每个内置运行模式 / 提词来源声明独立条目；
- core bundle 引入 prompts bundle；
- 默认 profile 下模式元数据与子代理提词来源来自注册表；
- overlay 禁用单个模式 / 来源条目真正生效；
- 条目 config 覆盖 label/export/order 与来源 export；
- 注册表接管 / 禁用 / 扩展 API。
"""

from __future__ import annotations

import pytest

import src.prompt_builder.builder as builder
from src.kernel import Kernel, materialize
from src.plugins.bootstrap import (
    build_kernel,
    inject_managed_config,
    resolve_entries,
    shutdown_kernel,
)
from src.plugins.manifest import PROMPT_MODE_ENTRIES, PROMPT_SOURCE_ENTRIES, build_config_tree


def _resolved(profile="cli"):
    return materialize(resolve_entries(profile, discover_external=False))


def test_manifest_declares_each_mode_and_source():
    from src.prompt_builder.modes import builtin_mode_names
    from src.prompt_builder.sources import builtin_prompt_source_ids

    modes, sources = [], []
    mode_ids, source_ids = [], []
    for entry, plug in _resolved():
        name = getattr(plug, "name", "")
        if name == "prompt_mode":
            modes.append((entry.config or {}).get("name"))
            mode_ids.append(entry.id)
        elif name == "prompt_source":
            sources.append((entry.config or {}).get("name"))
            source_ids.append(entry.id)
    assert set(modes) == set(builtin_mode_names())
    assert set(sources) == set(builtin_prompt_source_ids())
    assert all(i.startswith("prompts::prompt_mode_") for i in mode_ids)
    assert all(i.startswith("prompts::prompt_source_") for i in source_ids)


def test_manifest_entries_match_registry_declaration():
    from src.prompt_builder.modes import builtin_mode_names
    from src.prompt_builder.sources import builtin_prompt_source_ids

    assert {(e.get("config") or {}).get("name") for e in PROMPT_MODE_ENTRIES} == set(builtin_mode_names())
    assert {(e.get("config") or {}).get("name") for e in PROMPT_SOURCE_ENTRIES} == set(builtin_prompt_source_ids())


def test_tree_declares_prompts_bundle():
    tree = build_config_tree()
    assert "prompts" in tree.bundles()
    assert "prompts" in tree.bundle("core").includes


async def test_default_profile_mode_metadata_from_registry():
    kernel = await build_kernel("cli")
    original = builder.get_mode()
    try:
        builder.set_mode("simple")
        captured = {}

        def fake_build(agent_name, export_name, fallback, *args, **kwargs):
            captured["export"] = export_name
            return ["x"]

        import unittest.mock as mock

        with mock.patch.object(builder, "_build_prompt", fake_build):
            assert builder.build_system_prompt() == ["x"]
        assert captured["export"] == "prompts_export_main_simple"
        assert builder.mode_label("standard") == "标准模式"
    finally:
        builder.set_mode(original)
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


async def test_overlay_disable_prompt_mode():
    from src.prompt_builder.modes import active_modes

    kernel = await _build_with_disable(["prompts::prompt_mode_simple"])
    original = builder.get_mode()
    try:
        assert "simple" not in active_modes()
        assert builder.set_mode("simple") == "empty"
        assert builder.mode_order() == ["empty", "standard"]
    finally:
        builder.set_mode(original)
        await kernel.dispose()


async def test_overlay_disable_prompt_source_uses_fallback():
    from src.prompt_builder.sources import resolve_prompt_source

    kernel = await _build_with_disable(["prompts::prompt_source_map"])
    try:
        assert resolve_prompt_source("map") is None
        assert resolve_prompt_source("review") == "prompts_export_review"
    finally:
        await kernel.dispose()


def test_registry_api_roundtrip():
    from src.prompt_builder import modes as mreg
    from src.prompt_builder import sources as sreg

    mreg.reset()
    sreg.reset()
    try:
        from src.prompt_builder.modes import AgentMode
        from src.prompt_builder.sources import register_prompt_source

        undo = mreg.set_managed_builtin_modes(["simple"])
        assert "simple" not in mreg.active_modes()
        undo()
        assert "simple" in mreg.active_modes()

        undo2 = mreg.disable_builtin_modes(["standard"])
        assert "standard" not in mreg.active_modes()
        undo2()

        undo3 = mreg.register_mode(AgentMode("custom", "自定义", "prompts_export_main", 9))
        assert mreg.resolve_mode_export("custom") == "prompts_export_main"
        undo3()
        assert "custom" not in mreg.active_modes()

        undo4 = sreg.set_managed_builtin_prompt_sources(["plan"])
        assert sreg.resolve_prompt_source("plan") is None
        undo4()
        assert sreg.resolve_prompt_source("plan") == "prompts_export_plan"

        undo5 = register_prompt_source("my_agent", "my_export")
        assert sreg.resolve_prompt_source("my_agent") == "my_export"
        assert sreg.unregister_prompt_source("my_agent") is True
        undo5()
    finally:
        mreg.reset()
        sreg.reset()


async def test_entry_config_override():
    from src.plugins.config import apply as config_apply
    from src.plugins.prompt_entries import apply_prompt_mode, apply_prompt_source

    kernel = Kernel(name="t")
    kernel.mount(config_apply)
    kernel.mount(apply_prompt_mode, config={"name": "simple", "label": "简明模式", "export": "x_main", "order": 7})
    kernel.mount(apply_prompt_source, config={"name": "map", "export": "x_map"})
    await kernel.settle()
    try:
        from src.prompt_builder.modes import active_modes
        from src.prompt_builder.sources import resolve_prompt_source

        mode = active_modes()["simple"]
        assert mode.label == "简明模式"
        assert mode.export == "x_main"
        assert mode.order == 7
        assert resolve_prompt_source("map") == "x_map"
    finally:
        await kernel.dispose()
