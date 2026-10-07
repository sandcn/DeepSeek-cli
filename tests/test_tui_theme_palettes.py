"""扩展配色主题（nord / dracula / gruvbox）单元测试（2026-10-07 视觉美化）。

覆盖：
  - 内置主题注册表包含三个新主题（顺序与清单条目一致）；
  - 每个新调色板可按名解析且字段为 Style；
  - 清单 THEME_ENTRIES 与 ``builtin_theme_names()`` 同序同集；
  - 配置项候选表（CONFIG_ENTRY_OPTION_MAP）含三个新主题（/config 可选）；
  - /theme 命令描述表（CommandUiAdapter）含三个新主题中文描述。
"""

from __future__ import annotations

import pytest

import src.tui.core._theme as th


@pytest.fixture(autouse=True)
def _clean_theme():
    th.reset()
    yield
    th.reset()


NEW_THEMES = ("nord", "dracula", "gruvbox")


def test_builtin_theme_names_include_new():
    names = list(th.builtin_theme_names())
    for name in NEW_THEMES:
        assert name in names
    # 顺序：dark/light/high-contrast 在前（零回归），新主题追加在后
    assert names[:3] == ["dark", "light", "high-contrast"]


def test_new_palettes_resolve():
    for name in NEW_THEMES:
        palette = th.resolve_theme(name)
        assert isinstance(palette, th.Palette)
        assert palette.accent is not None
        assert palette.accent.fg is not None


def test_new_palette_cached_instance_stable():
    """同名主题多次解析返回同一实例（组件以 palette 作 memo 依赖）。"""
    assert th.resolve_theme("nord") is th.resolve_theme("nord")


def test_manifest_theme_entries_match_builtin_order():
    from src.plugins.manifest import THEME_ENTRIES

    declared = [entry["config"]["name"] for entry in THEME_ENTRIES]
    assert declared == list(th.builtin_theme_names())


def test_config_entry_options_include_new_themes():
    from src.presentation_data import CONFIG_ENTRY_OPTION_MAP

    names = [opt[0] for opt in CONFIG_ENTRY_OPTION_MAP["THEME"]]
    for name in NEW_THEMES:
        assert name in names


def test_ui_adapter_theme_desc_includes_new():
    from src.core.commands._ui_adapter import CommandUiAdapter

    pairs = dict(CommandUiAdapter().get_theme_names_with_desc())
    for name in NEW_THEMES:
        assert name in pairs
        assert pairs[name] != name  # 有中文描述（非回退名自身）


def test_theme_registry_names_tuple_matches():
    assert th.ThemeRegistry.names() == tuple(th.builtin_theme_names())
