"""终端按键解析（``bash_opt`` ``op=keys`` 的解析层）单元测试。

覆盖：光标/编辑/功能键标准序列、键名别名（esc/del/pageup/return …）、
紧凑写法（ctrl_c / ctrl-c）、修饰键组合（ctrl+shift+s / alt+f4 / ctrl+up）、
单个字符、控制码推导，以及不支持键与非法输入的报错。
"""

from __future__ import annotations

import pytest

from src.tools._terminal_keys import (
    SUPPORTED_TERMINAL_KEYS,
    parse_terminal_key,
)
from src.tools._window_input.result import ActionError


# ── 标准序列 ────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    # 光标键
    ("up", "\x1b[A"),
    ("down", "\x1b[B"),
    ("right", "\x1b[C"),
    ("left", "\x1b[D"),
    ("UP", "\x1b[A"),
    # 编辑键
    ("home", "\x1b[H"),
    ("end", "\x1b[F"),
    ("insert", "\x1b[2~"),
    ("delete", "\x1b[3~"),
    ("page_up", "\x1b[5~"),
    ("page_down", "\x1b[6~"),
    ("backspace", "\x7f"),
    ("tab", "\t"),
    ("enter", "\r"),
    ("escape", "\x1b"),
    ("space", " "),
    # 功能键
    ("f1", "\x1bOP"),
    ("f4", "\x1bOS"),
    ("f5", "\x1b[15~"),
    ("f12", "\x1b[24~"),
    ("f13", "\x1b[25~"),
    ("f20", "\x1b[34~"),
])
def test_standard_sequences(text: str, expected: str):
    assert parse_terminal_key(text) == expected


# ── 别名 ────────────────────────────────────────────────

@pytest.mark.parametrize("alias,canonical", [
    ("esc", "escape"),
    ("return", "enter"),
    ("cr", "enter"),
    ("del", "delete"),
    ("ins", "insert"),
    ("pageup", "page_up"),
    ("pgup", "page_up"),
    ("prior", "page_up"),
    ("pagedown", "page_down"),
    ("pgdn", "page_down"),
    ("next", "page_down"),
    ("bksp", "backspace"),
    ("spacebar", "space"),
])
def test_aliases_match_canonical(alias: str, canonical: str):
    """别名与规范名解析为同一序列（键名表统一到 _window_input.keys）。"""
    assert parse_terminal_key(alias) == parse_terminal_key(canonical)


# ── 控制组合 ────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("ctrl_a", "\x01"),
    ("ctrl-c", "\x03"),
    ("ctrl+c", "\x03"),
    ("CTRL_Z", "\x1a"),
    ("ctrl_l", "\x0c"),
    ("ctrl+space", "\x00"),
    ("ctrl+?", "\x7f"),
    ("ctrl+[", "\x1b"),
])
def test_control_combinations(text: str, expected: str):
    assert parse_terminal_key(text) == expected


def test_ctrl_shift_letter_same_as_ctrl_letter():
    """终端无法区分 ctrl+shift+字母 与 ctrl+字母（同发控制码）。"""
    assert parse_terminal_key("ctrl+shift+s") == parse_terminal_key("ctrl+s") == "\x13"


# ── 修饰键组合 ──────────────────────────────────────────

def test_alt_character_prefixes_escape():
    assert parse_terminal_key("alt+x") == "\x1bx"


def test_meta_character_prefixes_escape():
    assert parse_terminal_key("meta+a") == "\x1ba"


def test_shift_tab_is_back_tab():
    assert parse_terminal_key("shift+tab") == "\x1b[Z"


def test_modified_cursor_key_uses_xterm_parameter():
    assert parse_terminal_key("ctrl+up") == "\x1b[1;5A"
    assert parse_terminal_key("shift+up") == "\x1b[1;2A"


def test_modified_function_key_uses_xterm_parameter():
    # F4 无修饰是 SS3；带 Alt 退化为 CSI 1;3S
    assert parse_terminal_key("alt+f4") == "\x1b[1;3S"


# ── 单个字符 ────────────────────────────────────────────

@pytest.mark.parametrize("char", ["y", "n", "1", "!", "Z", "中"])
def test_single_character_passthrough(char: str):
    assert parse_terminal_key(char) == char


# ── 非法 / 不支持 ───────────────────────────────────────

@pytest.mark.parametrize("text", ["", "   ", "ctrl+", "ctrl++", "unknown_key",
                                  "notakey", "ctrl+notakey"])
def test_invalid_keys_raise(text: str):
    with pytest.raises(ActionError):
        parse_terminal_key(text)


@pytest.mark.parametrize("text", ["f21", "f24", "ctrl+1"])
def test_unsupported_keys_raise(text: str):
    """终端无对应序列的键给出明确错误（而非静默发送错误序列）。"""
    with pytest.raises(ActionError):
        parse_terminal_key(text)


def test_supported_keys_list_mentions_common_keys():
    for name in ("up", "down", "page_up", "enter", "escape", "f1", "f20"):
        assert name in SUPPORTED_TERMINAL_KEYS
