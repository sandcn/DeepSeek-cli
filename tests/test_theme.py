"""
主题颜色 256 色化单元测试。

验证 THEMES 预设中所有语义键已升级为 256 色 ANSI 码，
新增语义键在所有主题中完整存在。
"""
from __future__ import annotations

import pytest

from src.tui.core.theme import THEMES, THEME, set_theme, list_themes


# ── 辅助函数 ─────────────────────────────────────────────

_256_FG_RE = "\033[38;5;"
_256_BG_RE = "\033[48;5;"
_256_BOLD_FG_RE = "\033[1;38;5;"


def _is_256_color(value: str) -> bool:
    """判断颜色值是否为 256 色 ANSI 序列。"""
    return (
        value.startswith(_256_FG_RE)
        or value.startswith(_256_BG_RE)
        or value.startswith(_256_BOLD_FG_RE)
    )


# ── 基础键列表 ──────────────────────────────────────────

_BASE_KEYS = [
    "title", "subtitle", "prompt", "user", "assistant",
    "thinking", "tool", "success", "warning", "error",
    "info", "cost", "separator", "meta", "accent",
    "border", "highlight", "muted", "code", "divider",
]

_NEW_KEYS = [
    "progress_filled", "progress_empty",
    "diff_add", "diff_del", "diff_ctx",
    "border_active", "border_inactive",
    "overlay_bg", "tag_code",
]

_ALL_KEYS = _BASE_KEYS + _NEW_KEYS


# ── 测试类 ──────────────────────────────────────────────


class TestTheme256Upgrade:
    """验证 THEMES 预设已全部升级为 256 色。"""

    @pytest.mark.parametrize("theme_name", ["dark", "light", "high-contrast"])
    def test_all_values_are_256_color(self, theme_name: str):
        """每个主题的所有语义键值都是 256 色 ANSI 码。"""
        theme = THEMES[theme_name]
        for key, value in theme.items():
            assert _is_256_color(value), (
                f"{theme_name}.{key} = {value!r} 不是 256 色 ANSI 码"
            )

    @pytest.mark.parametrize("theme_name", ["dark", "light", "high-contrast"])
    def test_new_keys_exist_in_all_themes(self, theme_name: str):
        """新增语义键 progress_filled 等存在于所有主题中。"""
        theme = THEMES[theme_name]
        for key in _NEW_KEYS:
            assert key in theme, (
                f"{theme_name} 主题缺少新增键 {key}"
            )

    @pytest.mark.parametrize("theme_name", ["dark", "light", "high-contrast"])
    def test_all_required_keys_present(self, theme_name: str):
        """每个主题包含所有必需的基础键和新增键。"""
        theme = THEMES[theme_name]
        for key in _ALL_KEYS:
            assert key in theme, (
                f"{theme_name} 主题缺少必需键 {key}"
            )

    def test_code_and_tag_code_different(self):
        """各主题的 code 键和 tag_code 键不同。"""
        for theme_name in ["dark", "light", "high-contrast"]:
            theme = THEMES[theme_name]
            assert theme["code"] != theme["tag_code"], (
                f"{theme_name} 主题中 code 与 tag_code 值相同"
            )

    def test_themes_dict_format(self):
        """THEMES 字典格式不变：Dict[str, Dict[str, str]]。"""
        assert isinstance(THEMES, dict)
        for name, theme in THEMES.items():
            assert isinstance(name, str)
            assert isinstance(theme, dict)
            for key, value in theme.items():
                assert isinstance(key, str)
                assert isinstance(value, str)

    def test_list_themes_returns_all(self):
        """list_themes 返回所有主题名称。"""
        names = list_themes()
        assert "dark" in names
        assert "light" in names
        assert "high-contrast" in names
        assert len(names) >= 3


class TestThemeSwitch:
    """验证 set_theme 切换后所有键仍完整。"""

    @pytest.mark.parametrize("theme_name", ["dark", "light", "high-contrast"])
    def test_switch_preserves_all_keys(self, theme_name: str):
        """切换到主题后，THEME 包含所有必要键。"""
        set_theme(theme_name)
        for key in _ALL_KEYS:
            assert key in THEME, (
                f"切换到 {theme_name} 后 THEME 缺少键 {key}"
            )

    @pytest.mark.parametrize("theme_name", ["dark", "light", "high-contrast"])
    def test_switch_values_match_preset(self, theme_name: str):
        """切换到主题后，THEME 值与预设一致。"""
        set_theme(theme_name)
        for key in _ALL_KEYS:
            assert THEME[key] == THEMES[theme_name][key], (
                f"切换到 {theme_name} 后 THEME.{key} 与预设不一致"
            )

    def test_set_theme_unknown_raises(self):
        """设置未知主题抛出 ValueError。"""
        with pytest.raises(ValueError):
            set_theme("nonexistent")

    def test_theme_transition_independence(self):
        """主题切换不影响其他主题的预设值。"""
        dark_preset = dict(THEMES["dark"])
        set_theme("light")
        assert THEMES["dark"] == dark_preset, "切换到 light 后 dark 预设被修改"
        set_theme("high-contrast")
        assert THEMES["dark"] == dark_preset, "切换到 high-contrast 后 dark 预设被修改"
        assert THEMES["light"] != THEMES["dark"], "light 与 dark 主题不应相同"
