"""表现层数据条目化测试 — 每张数据表是清单中的独立插件条目。

覆盖：
- 清单为每张内置数据表声明独立条目；
- presentation bundle 引入 presentation_data bundle；
- 默认 profile 下 Emoji / 上下标 / HTML 配色 / 列表符号 / 轨迹表现 / 模式文本
  来自注册表；
- overlay 禁用单张表真正生效；
- 条目 config 整表替换；
- 注册表接管 / 禁用 / 扩展 API；
- 消费方（emoji_map / blocks / _special / trace_ledger / input_area）实时生效。
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
from src.plugins.manifest import PRESENTATION_DATA_ENTRIES, build_config_tree


def _resolved(profile="cli"):
    return materialize(resolve_entries(profile, discover_external=False))


def test_manifest_declares_each_table():
    from src.presentation_data import builtin_data_ids

    ids = []
    entry_ids = []
    for entry, plug in _resolved():
        if getattr(plug, "name", "") == "presentation_data_table":
            ids.append((entry.config or {}).get("id"))
            entry_ids.append(entry.id)
    assert set(ids) == set(builtin_data_ids())
    assert all(i.startswith("presentation_data::presentation_data_") for i in entry_ids)


def test_manifest_entries_match_registry_declaration():
    from src.presentation_data import builtin_data_ids

    assert {(e.get("config") or {}).get("id") for e in PRESENTATION_DATA_ENTRIES} == set(builtin_data_ids())


def test_tree_declares_presentation_data_bundle():
    tree = build_config_tree()
    assert "presentation_data" in tree.bundles()
    assert "presentation_data" in tree.bundle("presentation").includes


async def test_default_profile_tables():
    from src.presentation_data import (
        bullets,
        html_tag_color,
        mode_text,
        subscript_map,
        trace_kind_icon,
        trace_kind_name,
        trace_status_fg,
    )

    kernel = await build_kernel("cli")
    try:
        assert html_tag_color("div") == "blue"
        assert html_tag_color("unknown") == "bright_black"
        assert trace_kind_icon("tools") == "\U0001F9F0"
        assert trace_kind_name("reasoning") == "思考"
        assert trace_status_fg("fail") == 196
        assert mode_text("empty") == "空模式"
        assert bullets() == ["\u2022", "\u25e6", "\u25aa"]
        assert subscript_map()["0"] == "₀"
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


async def test_overlay_disable_emoji_table():
    from src.renderer.emoji_map import resolve_emoji

    kernel = await _build_with_disable(["presentation_data::presentation_data_emoji"])
    try:
        assert resolve_emoji(":smile:") == ":smile:"
    finally:
        await kernel.dispose()


async def test_overlay_disable_trace_kind_table():
    from src.presentation_data import trace_kind_icon

    kernel = await _build_with_disable(["presentation_data::presentation_data_trace_kind"])
    try:
        assert trace_kind_icon("tools") == "\u00b7"
    finally:
        await kernel.dispose()


def test_registry_api_roundtrip():
    from src.presentation_data import (
        DataTable,
        bullets,
        default_data_table,
        html_tag_color,
        reset,
    )
    from src.presentation_data import register_data_table, set_managed_builtin_data, disable_builtin_data

    reset()
    try:
        undo = set_managed_builtin_data(["html_tag_color"])
        assert html_tag_color("div") == "bright_black"
        undo()
        assert html_tag_color("div") == "blue"

        undo2 = disable_builtin_data(["bullet"])
        assert bullets() == []
        undo2()
        assert bullets() == ["\u2022", "\u25e6", "\u25aa"]

        undo3 = register_data_table(DataTable("custom_bullet", "bullet", ["*", "+"]))
        assert bullets() == ["*", "+"]
        undo3()
        assert bullets() == ["\u2022", "\u25e6", "\u25aa"]
    finally:
        reset()


async def test_entry_config_override():
    from src.plugins.config import apply as config_apply
    from src.plugins.presentation_data_entries import apply_presentation_data

    kernel = Kernel(name="t")
    kernel.mount(config_apply)
    kernel.mount(
        apply_presentation_data,
        config={"id": "mode_text", "data": {"empty": "E", "simple": "S", "standard": "N"}},
    )
    kernel.mount(
        apply_presentation_data,
        config={"id": "html_tag_color", "data": {"div": "red"}},
    )
    await kernel.settle()
    try:
        from src.presentation_data import html_tag_color, mode_text

        assert mode_text("empty") == "E"
        assert html_tag_color("div") == "red"
        assert html_tag_color("pre") == "bright_black"
    finally:
        await kernel.dispose()


async def test_consumers_live():
    from src.plugins.config import apply as config_apply
    from src.plugins.presentation_data_entries import apply_presentation_data

    kernel = Kernel(name="t")
    kernel.mount(config_apply)
    kernel.mount(apply_presentation_data, config={"id": "emoji", "data": {":zap2:": "\u26a1"}})
    await kernel.settle()
    try:
        from src.renderer.emoji_map import EMOJI_MAP, resolve_emoji

        assert resolve_emoji(":zap2:") == "\u26a1"
        assert ":zap2:" in EMOJI_MAP
    finally:
        await kernel.dispose()
