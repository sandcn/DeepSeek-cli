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


# ── 补齐的表现层数据表（工具显示名 / 告示样式 / Spinner / 配置项） ──


def test_new_tables_declared():
    from src.presentation_data import builtin_data_ids

    for table_id in (
        "tool_display_name", "admonition_style", "spinner_frames",
        "inline_spinner_frames", "config_entry_desc", "config_entry_option",
        "trace_kind_order", "trace_block_kind", "message_role_icon",
        "border_chars", "border_object_default",
    ):
        assert table_id in builtin_data_ids()
    assert {(e.get("config") or {}).get("id") for e in PRESENTATION_DATA_ENTRIES} == set(builtin_data_ids())


async def test_new_tables_default_profile():
    from src.core.tool_display import TOOL_DISPLAY_NAME, get_tool_display_name
    from src.presentation_data import (
        admonition_style,
        config_entry_descs,
        config_entry_options,
        inline_spinner_frames,
        spinner_preset,
    )
    from src.renderer.admonition import ADMONITION_STYLES, get_admonition_config

    kernel = await build_kernel("cli")
    try:
        assert get_tool_display_name("read_file") == "ReadFile"
        assert TOOL_DISPLAY_NAME["bash_opt"] == "BashOpt"
        assert get_admonition_config("warning")["color"] == "yellow"
        assert "NOTE" in ADMONITION_STYLES
        assert spinner_preset("dots") == "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        assert inline_spinner_frames().startswith("⠋")
        assert "MCP_SERVERS" in config_entry_descs()
        assert config_entry_options()["THEME"][0] == ("dark", "暗色主题")
    finally:
        await shutdown_kernel(kernel)


async def test_overlay_disable_tool_display_name():
    from src.core.tool_display import get_tool_display_name

    kernel = await _build_with_disable(
        ["presentation_data::presentation_data_tool_display_name"]
    )
    try:
        assert get_tool_display_name("read_file") == "read_file"
    finally:
        await kernel.dispose()


async def test_overlay_disable_admonition_style():
    from src.renderer.admonition import get_admonition_config

    kernel = await _build_with_disable(
        ["presentation_data::presentation_data_admonition_style"]
    )
    try:
        assert get_admonition_config("warning") == {}
    finally:
        await kernel.dispose()


async def test_entry_config_override_new_tables():
    from src.plugins.config import apply as config_apply
    from src.plugins.presentation_data_entries import apply_presentation_data

    kernel = Kernel(name="t")
    kernel.mount(config_apply)
    kernel.mount(
        apply_presentation_data,
        config={"id": "spinner_frames", "data": {"dots": "abcd"}},
    )
    kernel.mount(
        apply_presentation_data,
        config={"id": "config_entry_desc", "data": {"MCP_SERVERS": "自定义说明"}},
    )
    await kernel.settle()
    try:
        from src.config.view_model import CONFIG_ENTRY_DESCS
        from src.presentation_data import spinner_preset

        assert spinner_preset("dots") == "abcd"
        assert CONFIG_ENTRY_DESCS["MCP_SERVERS"] == "自定义说明"
    finally:
        await kernel.dispose()


async def test_new_tables_trace_role_border():
    from src.presentation_data import (
        border_chars,
        border_object_default,
        message_role_icons,
        trace_block_kind,
        trace_kind_order,
    )
    from src.tui.app.trace_types import _BLOCK_KIND_MAP, TRACE_KIND_ORDER
    from src.tui.pipeline.message_display import _DEFAULT_ROLE_MAP

    kernel = await build_kernel("cli")
    try:
        assert trace_kind_order()[0] == "tools"
        assert "tool" in TRACE_KIND_ORDER
        assert trace_block_kind()["parse_info"] == "context"
        assert _BLOCK_KIND_MAP["error"] == "system"
        assert message_role_icons()["user"] == "\u25cf"
        assert _DEFAULT_ROLE_MAP["assistant"] == "\u25c6"
        assert border_chars()["double"][0] == "╔"
        assert border_object_default()["topLeft"] == "┌"
    finally:
        await shutdown_kernel(kernel)


async def test_overlay_disable_border_chars():
    from src.presentation_data import border_chars, message_role_icons

    kernel = await _build_with_disable([
        "presentation_data::presentation_data_border_chars",
        "presentation_data::presentation_data_message_role_icon",
    ])
    try:
        assert border_chars() == {}
        assert message_role_icons() == {}
    finally:
        await kernel.dispose()


async def test_config_entry_descs_extra_keys():
    from src.config.view_model import CONFIG_ENTRY_DESCS, build_config_entries

    entries = {e["key"]: e for e in build_config_entries()}
    assert entries["provider"]["desc"] == CONFIG_ENTRY_DESCS["provider"]
    assert entries["provider"]["desc"]
    assert entries["api_key"]["desc"]
