"""终端按键名 → ANSI/VT100 字节序列（``bash_opt`` 的 ``op=keys`` 解析层）。

把按键名（``ctrl+c`` / ``ctrl_c`` / ``up`` / ``f5`` / ``shift+tab`` / 单个
字符 …）解析为终端输入字节序列（ECMA-48 / xterm 约定）。这些序列在 Linux、
macOS 的 PTY 与 Windows 的 ConPTY 中被统一接受，不依赖平台特定 API。

键名规范化（别名表、组合键语法）复用 GUI 输入层（``_window_input.keys``）：
``op=keys`` 与 ``op=key`` 因此共享同一套键名规则——``esc`` / ``del`` /
``pageup`` / ``ctrl+c`` 等写法在两种操作下行为一致，不会出现「同一个键名
在终端与 GUI 下解析不同」的漂移。

扩展方式：
  - 新增带 CSI 参数的特殊键 → 在 :data:`_SPECIAL_KEYS` 增加一条
    ``键名 -> (序列类型, 参数)``；
  - 新增无参数简单键 → 在 :data:`_SIMPLE_KEYS` 增加；
  - 单个字符与 ``ctrl`` / ``alt`` / ``shift`` 组合由
    :func:`_character_sequence` 统一推导，无需逐键登记。
"""

from __future__ import annotations

from ._window_input.keys import (
    Shortcut,
    parse_shortcut,
    shift_character,
)
from ._window_input.result import ActionError

#: 特殊键 → ``(序列类型, 参数)``
#:   ``final``：``CSI [1;<m>]<参数>``（光标键 / Home / End）
#:   ``tilde``：``CSI <参数>[;<m>]~``（Insert / Delete / PageUp / PageDown / 功能键）
#:   ``ss3``  ：``SS3 <参数>``，带修饰时退化为 ``CSI 1;<m><参数>``（F1-F4）
_SPECIAL_KEYS: dict[str, tuple[str, str]] = {
    "up": ("final", "A"),
    "down": ("final", "B"),
    "right": ("final", "C"),
    "left": ("final", "D"),
    "home": ("final", "H"),
    "end": ("final", "F"),
    "insert": ("tilde", "2"),
    "delete": ("tilde", "3"),
    "page_up": ("tilde", "5"),
    "page_down": ("tilde", "6"),
    "f1": ("ss3", "P"),
    "f2": ("ss3", "Q"),
    "f3": ("ss3", "R"),
    "f4": ("ss3", "S"),
    "f5": ("tilde", "15"),
    "f6": ("tilde", "17"),
    "f7": ("tilde", "18"),
    "f8": ("tilde", "19"),
    "f9": ("tilde", "20"),
    "f10": ("tilde", "21"),
    "f11": ("tilde", "23"),
    "f12": ("tilde", "24"),
    "f13": ("tilde", "25"),
    "f14": ("tilde", "26"),
    "f15": ("tilde", "28"),
    "f16": ("tilde", "29"),
    "f17": ("tilde", "31"),
    "f18": ("tilde", "32"),
    "f19": ("tilde", "33"),
    "f20": ("tilde", "34"),
}

#: 简单键（无 CSI 修饰参数）→ 基础字节序列
_SIMPLE_KEYS: dict[str, str] = {
    "enter": "\r",
    "escape": "\x1b",
    "tab": "\t",
    "space": " ",
    "backspace": "\x7f",
}

#: ``ctrl+<字符>`` 的控制码（字符已按 Shift 推导；字母由 :func:`_control_code` 程序化生成）
_CONTROL_CHARS: dict[str, str] = {
    "@": "\x00", " ": "\x00", "2": "\x00",
    "[": "\x1b", "3": "\x1b",
    "\\": "\x1c", "4": "\x1c",
    "]": "\x1d", "5": "\x1d",
    "^": "\x1e", "6": "\x1e",
    "_": "\x1f", "-": "\x1f", "/": "\x1f", "7": "\x1f",
    "?": "\x7f", "8": "\x7f",
}

#: 终端可解析的按键名（错误提示用，稳定顺序）
SUPPORTED_TERMINAL_KEYS: tuple[str, ...] = (
    "up", "down", "left", "right", "home", "end",
    "page_up", "page_down", "insert", "delete",
    "backspace", "tab", "enter", "escape", "space",
    *(f"f{index}" for index in range(1, 21)),
)


def parse_terminal_key(text: str) -> str:
    """把按键名解析为终端输入字节序列。

    支持：修饰键组合（``ctrl+shift+s`` / ``alt+f4`` / ``shift+tab``）、
    紧凑写法（``ctrl_c`` / ``ctrl-c``）、常用别名（``esc`` / ``del`` /
    ``pageup`` / ``return`` / ``ins`` …）、单个字符（``y`` / ``1``）与
    ``f1``-``f20``。

    Raises:
        ActionError: 键名为空、组合键格式非法、键名未知，或该键在终端中
            无对应序列（如 ``f21``-``f24``）。
    """
    shortcut = parse_shortcut(text)
    sequence = _shortcut_sequence(shortcut)
    if sequence is None:
        raise ActionError(
            f"终端不支持按键 {shortcut.display()!r}"
            f"（面向 GUI 窗口请改用 op=key）"
        )
    return sequence


def _shortcut_sequence(shortcut: Shortcut) -> str | None:
    """组合键 → 终端字节序列（无对应序列时返回 None）。"""
    key = shortcut.key
    modifiers = shortcut.modifiers
    if shortcut.is_character:
        return _character_sequence(key, modifiers)
    if key == "tab" and "shift" in modifiers:
        return _back_tab_sequence(modifiers)
    if key in _SIMPLE_KEYS:
        return _simple_sequence(key, modifiers)
    entry = _SPECIAL_KEYS.get(key)
    if entry is None:
        return None
    return _special_sequence(entry[0], entry[1], modifiers)


def _character_sequence(char: str, modifiers: tuple[str, ...]) -> str | None:
    """单字符主键 → 序列（Shift 先改变字符，Ctrl 再取控制码）。"""
    mod_set = set(modifiers)
    if "shift" in mod_set:
        char = shift_character(char)
    if "ctrl" in mod_set:
        code = _control_code(char)
        if code is None:
            return None
        return "\x1b" + code if {"alt", "meta"} & mod_set else code
    if {"alt", "meta"} & mod_set:
        return "\x1b" + char
    return char


def _simple_sequence(key: str, modifiers: tuple[str, ...]) -> str:
    """简单键（enter/escape/tab/space/backspace）→ 序列。"""
    base = _SIMPLE_KEYS[key]
    mod_set = set(modifiers)
    if "ctrl" in mod_set and not ({"alt", "meta"} & mod_set):
        code = _control_code(base)
        if code is not None:
            return code
    if {"alt", "meta"} & mod_set:
        if "ctrl" in mod_set:
            code = _control_code(base)
            if code is not None:
                return "\x1b" + code
        return "\x1b" + base
    if "shift" in mod_set:
        base = shift_character(base)
    return base


def _special_sequence(kind: str, parameter: str,
                      modifiers: tuple[str, ...]) -> str:
    """光标 / 编辑 / 功能键 → xterm 序列（带修饰时附修饰参数）。"""
    modifier = _xterm_modifier(modifiers)
    if kind == "tilde":
        if modifier == 1:
            return f"\x1b[{parameter}~"
        return f"\x1b[{parameter};{modifier}~"
    if kind == "final":
        if modifier == 1:
            return f"\x1b[{parameter}"
        return f"\x1b[1;{modifier}{parameter}"
    # ss3：F1-F4 无修饰为 SS3，带修饰退化为 CSI 1;<m><final>
    if modifier == 1:
        return f"\x1bO{parameter}"
    return f"\x1b[1;{modifier}{parameter}"


def _back_tab_sequence(modifiers: tuple[str, ...]) -> str:
    """Shift+Tab（CBT）。仅 Shift 时是裸 ``CSI Z``，带其它修饰附参数。"""
    if modifiers == ("shift",):
        return "\x1b[Z"
    return f"\x1b[1;{_xterm_modifier(modifiers)}Z"


def _xterm_modifier(modifiers: tuple[str, ...]) -> int:
    """xterm 修饰参数：``1 + shift(1) + alt(2) + ctrl(4) + meta(8)``。"""
    value = 1
    if "shift" in modifiers:
        value += 1
    if "alt" in modifiers:
        value += 2
    if "ctrl" in modifiers:
        value += 4
    if "meta" in modifiers:
        value += 8
    return value


def _control_code(char: str) -> str | None:
    """``ctrl+<字符>`` 的控制码（字母程序化推导，其余查表）。"""
    if len(char) != 1:
        return None
    if "a" <= char <= "z":
        return chr(ord(char) - ord("a") + 1)
    if "A" <= char <= "Z":
        return chr(ord(char) - ord("A") + 1)
    return _CONTROL_CHARS.get(char)


__all__ = [
    "SUPPORTED_TERMINAL_KEYS",
    "parse_terminal_key",
]
