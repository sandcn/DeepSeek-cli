"""运行期默认值条目化测试 — 计费/指标/UI 默认参数各一张数据表条目。

覆盖：
- 三张新表进入注册表声明与清单条目；
- 默认 profile 下各消费方实时读取表值；
- 条目 config.data 整表替换真正生效；
- overlay 禁用整表后消费方回退兜底字面量；
- 非法值防御（不崩溃、回退）。
"""

from __future__ import annotations

from src.kernel import Kernel, materialize
from src.plugins.bootstrap import (
    build_kernel,
    inject_managed_config,
    resolve_entries,
    shutdown_kernel,
)
from src.plugins.manifest import PRESENTATION_DATA_ENTRIES, build_config_tree

NEW_TABLES = ("billing_default", "metric_defaults", "ui_defaults")


def test_new_default_tables_declared():
    from src.presentation_data import builtin_data_ids

    for table_id in NEW_TABLES:
        assert table_id in builtin_data_ids()
    assert {(e.get("config") or {}).get("id") for e in PRESENTATION_DATA_ENTRIES} == set(builtin_data_ids())
    tree = build_config_tree()
    entry_ids = {entry.id for entry in tree.resolve("cli")}
    for table_id in NEW_TABLES:
        assert f"presentation_data::presentation_data_{table_id}" in entry_ids


async def test_default_profile_runtime_defaults():
    from src.api.telemetry import _estimate_cost
    from src.config.view_model import _default_truncate
    from src.core.telemetry.metrics import _default_percentiles
    from src.presentation_data import billing_defaults, metric_defaults, ui_defaults
    from src.prompt_builder.project_summary import _default_max_tokens
    from src.tui._input_layout import _prompt_of
    from src.tui.app.toolcard import _category_default_breath, _category_default_style
    from src.tui.ink.widgets._badge_divider import _divider_default_width

    kernel = await build_kernel("cli")
    try:
        assert billing_defaults()["input_per_1m"] == 0.55
        assert metric_defaults()["percentiles"] == [50, 90, 95, 99]
        assert ui_defaults()["prompt"] == "> "
        assert _prompt_of({}) == "> "
        assert _divider_default_width() == 40
        assert _default_truncate() == 48
        assert _default_max_tokens() == 8000
        assert _category_default_style().fg == 242
        assert _category_default_breath() == (242, 252)
        assert _default_percentiles() == [50, 90, 95, 99]
        assert round(_estimate_cost("no-such-model", 1_000_000, 1_000_000), 4) == round(0.55 + 2.19, 4)
    finally:
        await shutdown_kernel(kernel)


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


async def test_overlay_disable_ui_defaults_falls_back():
    from src.api.telemetry import _default_prices
    from src.config.view_model import _default_truncate
    from src.presentation_data import ui_defaults
    from src.prompt_builder.project_summary import _default_max_tokens
    from src.tui._input_layout import _prompt_of
    from src.tui.ink.widgets._badge_divider import _divider_default_width

    kernel = await _build_with_overlay(
        {"disable": ["presentation_data::presentation_data_ui_defaults"]}
    )
    try:
        assert ui_defaults() == {}
        assert _prompt_of({}) == "> "
        assert _divider_default_width() == 40
        assert _default_truncate() == 48
        assert _default_max_tokens() == 8000
        assert _default_prices() == (0.55, 2.19)
    finally:
        await kernel.dispose()


async def test_overlay_disable_metric_defaults_falls_back():
    from src.core.telemetry.metrics import _default_percentiles
    from src.presentation_data import metric_defaults

    kernel = await _build_with_overlay(
        {"disable": ["presentation_data::presentation_data_metric_defaults"]}
    )
    try:
        assert metric_defaults() == {}
        assert _default_percentiles() == [50, 90, 95, 99]
    finally:
        await kernel.dispose()


async def test_entry_config_override_defaults():
    from src.config.view_model import _default_truncate
    from src.plugins.config import apply as config_apply
    from src.plugins.presentation_data_entries import apply_presentation_data
    from src.prompt_builder.project_summary import _default_max_tokens
    from src.tui._input_layout import _prompt_of
    from src.tui.app.toolcard import _category_default_breath, _category_default_style
    from src.tui.ink.widgets._badge_divider import _divider_default_width
    from src.tui.ink.widgets.codeblock import _default_border

    kernel = Kernel(name="t")
    kernel.mount(config_apply)
    kernel.mount(apply_presentation_data, config={
        "id": "ui_defaults",
        "data": {
            "prompt": "$$ ",
            "truncate_width": 20,
            "divider_width": 12,
            "summary_max_tokens": 123,
            "tool_fallback_fg": 100,
            "tool_fallback_breath": [100, 110],
            "codeblock_border": ["a", "b", "c", "d", "e", "f"],
        },
    })
    kernel.mount(apply_presentation_data, config={
        "id": "billing_default",
        "data": {"input_per_1m": 1.0, "output_per_1m": 2.0},
    })
    kernel.mount(apply_presentation_data, config={
        "id": "metric_defaults",
        "data": {"percentiles": [10, 20]},
    })
    await kernel.settle()
    try:
        from src.api.telemetry import _default_prices
        from src.core.telemetry.metrics import _default_percentiles

        assert _prompt_of({}) == "$$ "
        assert _divider_default_width() == 12
        assert _default_truncate() == 20
        assert _default_max_tokens() == 123
        assert _category_default_style().fg == 100
        assert _category_default_breath() == (100, 110)
        assert _default_border() == ("a", "b", "c", "d", "e", "f")
        assert _default_prices() == (1.0, 2.0)
        assert _default_percentiles() == [10, 20]
    finally:
        await kernel.dispose()


async def test_invalid_values_fall_back():
    from src.config.view_model import _default_truncate
    from src.core.telemetry.metrics import _default_percentiles
    from src.plugins.config import apply as config_apply
    from src.plugins.presentation_data_entries import apply_presentation_data
    from src.prompt_builder.project_summary import _default_max_tokens
    from src.tui._input_layout import _prompt_of
    from src.tui.ink.widgets._badge_divider import _divider_default_width

    kernel = Kernel(name="t")
    kernel.mount(config_apply)
    kernel.mount(apply_presentation_data, config={
        "id": "ui_defaults",
        "data": {
            "prompt": "", "truncate_width": "bad", "divider_width": 0,
            "summary_max_tokens": -1, "tool_fallback_breath": [1],
        },
    })
    kernel.mount(apply_presentation_data, config={
        "id": "metric_defaults", "data": {"percentiles": ["x"]},
    })
    await kernel.settle()
    try:
        assert _prompt_of({}) == "> "
        assert _divider_default_width() == 40
        assert _default_truncate() == 48
        assert _default_max_tokens() == 8000
        assert _default_percentiles() == [50, 90, 95, 99]
    finally:
        await kernel.dispose()
