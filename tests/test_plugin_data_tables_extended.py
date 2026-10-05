"""扩展数据表条目化测试 — 语义色/渐变/Shell 检测/错误提示/Badge/Kitty/
嵌套符号/Diff 样式/轨迹样式/UI 默认参数扩展。

覆盖：
- 九张新表进入注册表声明与清单条目；
- 默认 profile 下各消费方实时读取表值；
- 条目 config.data 覆盖真正生效；
- overlay 禁用整表后消费方回退兜底字面量。
"""

from __future__ import annotations

from src.kernel import Kernel, materialize
from src.plugins.bootstrap import (
    build_kernel,
    inject_managed_config,
    resolve_entries,
    shutdown_kernel,
)
from src.plugins.manifest import PRESENTATION_DATA_ENTRIES

NEW_TABLES = (
    "semantic_color", "gradient_stops", "shell_detect", "http_error_hint",
    "badge_metrics", "kitty_protocol", "nested_bullet", "diff_style", "trace_style",
    "model_patterns",
)


def test_new_tables_declared():
    from src.presentation_data import builtin_data_ids

    for table_id in NEW_TABLES:
        assert table_id in builtin_data_ids()
    assert {(e.get("config") or {}).get("id") for e in PRESENTATION_DATA_ENTRIES} == set(builtin_data_ids())


async def test_default_profile_consumers():
    from src.api.errors import _status_hints
    from src.mcp.config import valid_agent_types, valid_transports
    from src.presentation_data import (
        badge_metrics,
        gradient_param,
        kitty_protocol,
        nested_bullets,
        semantic_color,
        ui_default,
    )
    from src.prompt_builder import shell_info
    from src.tools.page_fetcher import date_patterns
    from src.tui._const import _SEMANTIC_COLOR
    from src.tui._diff_renderer import delimiter_width, diff_style_map
    from src.tui._input_parser import decode_kitty_modifiers
    from src.tui.app.header import _gradient_stops
    from src.tui.app.trace_styles import search_query_max, trace_style_map

    kernel = await build_kernel("cli")
    try:
        assert _SEMANTIC_COLOR["accent"] == 45
        assert semantic_color("branch") == 239
        assert _gradient_stops() == (45, 39, 141, 213)
        assert gradient_param("header_dot")["period"] == 6.0
        assert shell_info._detect_table("shell_aliases", {})["bash"] == "bash"
        assert "请求频率超限" in _status_hints()["429"]
        assert badge_metrics()["fg_on_light"] == 232
        assert kitty_protocol()["event_types"] == ["press", "repeat", "release"]
        assert decode_kitty_modifiers(3)["shift"] is True
        assert decode_kitty_modifiers(3)["alt"] is True
        assert nested_bullets()[0] == "\u2022"
        assert diff_style_map()["_DIFF_FILE_OLD"].fg == 210
        assert delimiter_width() == 40
        assert trace_style_map()["_S_TITLE"].fg == 45
        assert search_query_max() == 200
        assert ui_default("role_labels")["user"] == "用户"
        assert ui_default("tool_head_tools") == ["read_file"]
        assert "/editmsg" in ui_default("internal_prefill_cmds")
        assert ui_default("summary_truncate_length") == 300
        assert {"stdio", "http", "sse"} <= set(valid_transports())
        assert {"map", "review", "plan", "execute"} <= set(valid_agent_types())
        assert "%Y-%m-%d" in date_patterns()
        from src.api.adapters._utils import is_deepseek_v4_model, reasoner_patterns

        assert is_deepseek_v4_model("deepseek-v4-pro") is True
        assert is_deepseek_v4_model("gpt-4o") is False
        assert "reasoner" in reasoner_patterns()
        from src.presentation_data import model_pattern

        assert model_pattern("v4_prefixes") == ["deepseek-v4", "deepseek-flash"]
    finally:
        await shutdown_kernel(kernel)


async def test_entry_config_override():
    from src.plugins.config import apply as config_apply
    from src.plugins.presentation_data_entries import apply_presentation_data
    from src.presentation_data import (
        badge_metrics,
        gradient_param,
        nested_bullets,
        semantic_color,
        ui_default,
    )
    from src.prompt_builder import shell_info
    from src.tui._const import _SEMANTIC_COLOR
    from src.tui._diff_renderer import delimiter_width
    from src.tui._input_parser import _kitty_event_types
    from src.tui.app.header import _gradient_stops

    kernel = Kernel(name="t")
    kernel.mount(config_apply)
    kernel.mount(apply_presentation_data, config={
        "id": "semantic_color", "data": {"accent": 99},
    })
    kernel.mount(apply_presentation_data, config={
        "id": "gradient_stops", "data": {"title_stops": [1, 2, 3]},
    })
    kernel.mount(apply_presentation_data, config={
        "id": "shell_detect", "data": {"shell_aliases": {"mysh": "zsh"}},
    })
    kernel.mount(apply_presentation_data, config={
        "id": "badge_metrics", "data": {"fg_on_light": 100, "brightness_threshold": 0},
    })
    kernel.mount(apply_presentation_data, config={
        "id": "kitty_protocol", "data": {"event_types": ["down"]},
    })
    kernel.mount(apply_presentation_data, config={
        "id": "nested_bullet", "data": ["A", "B"],
    })
    kernel.mount(apply_presentation_data, config={
        "id": "diff_style", "data": {"separator_width": 12, "file_old": {"fg": 7}},
    })
    kernel.mount(apply_presentation_data, config={
        "id": "trace_style", "data": {"search_query_max": 42},
    })
    kernel.mount(apply_presentation_data, config={
        "id": "model_patterns", "data": {"v4_prefixes": ["my-v4"]},
    })
    await kernel.settle()
    try:
        assert semantic_color("accent") == 99
        assert _SEMANTIC_COLOR["accent"] == 99
        assert _gradient_stops() == (1, 2, 3)
        assert gradient_param("title_stops") == [1, 2, 3]
        assert shell_info._detect_table("shell_aliases", {}) == {"mysh": "zsh"}
        assert badge_metrics()["fg_on_light"] == 100
        assert _kitty_event_types() == ["down"]
        assert nested_bullets() == ["A", "B"]
        assert delimiter_width() == 12
        assert ui_default("role_labels") is None or isinstance(ui_default("role_labels"), dict)

        from src.api.adapters._utils import is_deepseek_v4_model, v4_prefixes

        assert v4_prefixes() == ("my-v4",)
        assert is_deepseek_v4_model("my-v4-pro") is True
        assert is_deepseek_v4_model("deepseek-v4-pro") is False
    finally:
        await kernel.dispose()


async def _build_with_overlay(overlay):
    from src.kernel.overlay import apply_overlay

    entries = apply_overlay(resolve_entries("cli", discover_external=False), overlay)
    resolved = materialize(entries)
    inject_managed_config(resolved)
    kernel = Kernel(name="t", profile="cli")
    for entry, plug in resolved:
        if entry.disabled:
            continue
        kernel.mount(plug, config=entry.config)
    await kernel.settle()
    return kernel


async def test_overlay_disable_falls_back():
    from src.presentation_data import (
        badge_metrics,
        gradient_params,
        kitty_protocol,
        nested_bullets,
        semantic_colors,
    )
    from src.tui._const import _SEMANTIC_COLOR
    from src.tui.app.header import _gradient_stops

    kernel = await _build_with_overlay({"disable": [
        "presentation_data::presentation_data_semantic_color",
        "presentation_data::presentation_data_gradient_stops",
        "presentation_data::presentation_data_badge_metrics",
        "presentation_data::presentation_data_kitty_protocol",
        "presentation_data::presentation_data_nested_bullet",
    ]})
    try:
        assert semantic_colors() == {}
        assert gradient_params() == {}
        assert badge_metrics() == {}
        assert kitty_protocol() == {}
        assert _SEMANTIC_COLOR["accent"] == 45
        assert _gradient_stops() == (45, 39, 141, 213)
        assert nested_bullets()[0] == "\u2022"
        assert len(nested_bullets()) == 6
    finally:
        await kernel.dispose()


async def test_badge_calculation_uses_table():
    from src.plugins.config import apply as config_apply
    from src.plugins.presentation_data_entries import apply_presentation_data
    from src.tui.ink.widgets._badge_divider import _badge_fg_for_bg

    kernel = Kernel(name="t")
    kernel.mount(config_apply)
    kernel.mount(apply_presentation_data, config={
        "id": "badge_metrics",
        "data": {"fg_on_dark": 11, "fg_on_light": 22, "brightness_threshold": 150070},
    })
    await kernel.settle()
    try:
        assert _badge_fg_for_bg(0) == 11      # 阈值极大 → 全部按暗背景
    finally:
        await kernel.dispose()

    kernel = Kernel(name="t")
    kernel.mount(config_apply)
    kernel.mount(apply_presentation_data, config={
        "id": "badge_metrics",
        "data": {"fg_on_dark": 11, "fg_on_light": 22, "brightness_threshold": -1},
    })
    await kernel.settle()
    try:
        assert _badge_fg_for_bg(0) == 22      # 阈值极小 → 全部按亮背景
    finally:
        await kernel.dispose()
