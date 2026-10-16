"""窗口输入：按键名解析与跨平台键码表测试。

覆盖：组合键语法（ctrl+shift+s / 紧凑 ctrl_c / ctrl-c）、别名归一、
修饰键解析（字符串与数组）、非法输入报错，以及各平台键码表对规范键名的
覆盖完整性（Windows / X11 要求全覆盖，macOS 允许少量平台不存在的键）。
"""

from __future__ import annotations

import pytest

from src.tools._window_input.keys import (
    KEY_ALIASES,
    KNOWN_KEYS,
    MACOS_KEYCODE,
    MACOS_MODIFIER_NAMES,
    MODIFIER_ORDER,
    WINDOWS_VK,
    X11_KEYSYM,
    classify_token,
    parse_modifiers,
    parse_shortcut,
)
from src.tools._window_input.result import ActionError


# ── 组合键解析 ───────────────────────────────────────────

def test_parse_shortcut_with_modifiers():
    shortcut = parse_shortcut("ctrl+shift+s")
    assert shortcut.modifiers == ("ctrl", "shift")
    assert shortcut.key == "s"
    assert shortcut.is_character
    assert shortcut.display() == "ctrl+shift+s"


def test_parse_shortcut_orders_modifiers():
    """修饰键按固定按下顺序排列（ctrl → alt → shift → meta）。"""
    shortcut = parse_shortcut("shift+ctrl+s")
    assert shortcut.modifiers == ("ctrl", "shift")


def test_parse_shortcut_single_named_key():
    shortcut = parse_shortcut("enter")
    assert shortcut.modifiers == ()
    assert shortcut.key == "enter"
    assert not shortcut.is_character


def test_parse_shortcut_alias_normalization():
    assert parse_shortcut("esc").key == "escape"
    assert parse_shortcut("Return").key == "enter"
    assert parse_shortcut("pgup").key == "page_up"


def test_parse_shortcut_compact_forms():
    assert parse_shortcut("ctrl_c").modifiers == ("ctrl",)
    assert parse_shortcut("ctrl_c").key == "c"
    assert parse_shortcut("ctrl-c").key == "c"
    assert parse_shortcut("ctrl_page_down").key == "page_down"


def test_parse_shortcut_compact_keeps_known_key():
    """已知键名中的连字符不被误拆（page_down 不是 ctrl 组合）。"""
    shortcut = parse_shortcut("page_down")
    assert shortcut.modifiers == ()
    assert shortcut.key == "page_down"


def test_parse_shortcut_function_key_with_modifier():
    shortcut = parse_shortcut("alt+f4")
    assert shortcut.modifiers == ("alt",)
    assert shortcut.key == "f4"


def test_parse_shortcut_case_insensitive_and_spaces():
    shortcut = parse_shortcut(" Ctrl + Alt + Delete ")
    assert shortcut.modifiers == ("ctrl", "alt")
    assert shortcut.key == "delete"


def test_parse_shortcut_character_keeps_case():
    assert parse_shortcut("S").key == "S"


def test_parse_shortcut_empty_raises():
    with pytest.raises(ActionError):
        parse_shortcut("   ")


def test_parse_shortcut_modifier_only_raises():
    with pytest.raises(ActionError):
        parse_shortcut("ctrl")


def test_parse_shortcut_unknown_key_raises():
    with pytest.raises(ActionError) as excinfo:
        parse_shortcut("ctrl+nope")
    assert "未知按键" in str(excinfo.value)


def test_parse_shortcut_key_before_modifier_raises():
    with pytest.raises(ActionError):
        parse_shortcut("ctrl+s+t")


# ── 修饰键参数解析 ───────────────────────────────────────

def test_parse_modifiers_from_string_and_list():
    assert parse_modifiers("ctrl+shift") == ("ctrl", "shift")
    assert parse_modifiers(["shift", "ctrl"]) == ("ctrl", "shift")
    assert parse_modifiers("ctrl,alt") == ("ctrl", "alt")
    assert parse_modifiers(None) == ()
    assert parse_modifiers([]) == ()


def test_parse_modifiers_dedupes():
    assert parse_modifiers(["ctrl", "ctrl"]) == ("ctrl",)


def test_parse_modifiers_rejects_non_modifier():
    with pytest.raises(ActionError):
        parse_modifiers(["ctrl", "s"])


def test_parse_modifiers_rejects_bad_type():
    with pytest.raises(ActionError):
        parse_modifiers(123)


# ── classify_token ──────────────────────────────────────

def test_classify_token_kinds():
    assert classify_token("ctrl") == ("modifier", "ctrl")
    assert classify_token("enter") == ("key", "enter")
    assert classify_token("a") == ("char", "a")


def test_classify_token_empty_raises():
    with pytest.raises(ActionError):
        classify_token("  ")


# ── 键码表覆盖 ───────────────────────────────────────────

def test_windows_vk_covers_all_known_keys():
    assert KNOWN_KEYS <= set(WINDOWS_VK)
    assert set(MODIFIER_ORDER) <= set(WINDOWS_VK)


def test_x11_keysym_covers_all_known_keys():
    assert KNOWN_KEYS <= set(X11_KEYSYM)
    assert set(MODIFIER_ORDER) <= set(X11_KEYSYM)


def test_macos_keycode_covers_common_keys():
    assert {"enter", "escape", "tab", "space", "up", "down", "left", "right",
            "f1", "f12"} <= set(MACOS_KEYCODE)
    # macOS 无这些键的虚拟键码：显式缺席（后端会给出明确错误）
    assert {"print_screen", "pause", "menu"} & set(MACOS_KEYCODE) == set()
    assert set(MODIFIER_ORDER) <= set(MACOS_MODIFIER_NAMES)


def test_alias_table_values_are_canonical():
    for alias, canonical in KEY_ALIASES.items():
        assert alias == alias.lower()
        assert canonical == canonical.lower()
