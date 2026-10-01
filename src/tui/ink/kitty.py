"""kitty — kitty 键盘协议常量与工具（React Ink v6/v7 ``kittyFlags`` 等价）。

官方 React Ink 导出 ``kittyFlags`` / ``kittyModifiers`` / ``resolveFlags`` 与
``KittyKeyboardOptions``；本模块对齐同名 API，并提供协议启用/禁用的终端
控制序列构建（供 ``render({kittyKeyboard})`` 使用）。

参考协议：https://sw.kovidgoyal.net/kitty/keyboard-protocol/
"""

from __future__ import annotations

from typing import Any, Iterable

#: 协议增强标志（``render({kittyKeyboard: {flags: [...]}})`` 可用名）。
kittyFlags = {
    "disambiguateEscapeCodes": 1,
    "reportEventTypes": 2,
    "reportAlternateKeys": 4,
    "reportAllKeysAsEscapeCodes": 8,
    "reportAssociatedText": 16,
}

#: 修饰键位掩码（kitty CSI-u 的 ``modifier = 1 + 位掩码``）。
kittyModifiers = {
    "shift": 1,
    "alt": 2,
    "ctrl": 4,
    "super": 8,
    "hyper": 16,
    "meta": 32,
    "capsLock": 64,
    "numLock": 128,
}


def resolveFlags(flags: Iterable[str] | None) -> int:
    """把标志名列表解析为位掩码（未知名忽略，None → 0）。"""
    if not flags:
        return 0
    total = 0
    for name in flags:
        total |= kittyFlags.get(name, 0)
    return total


def encode_modifiers(bits: int) -> int:
    """把位掩码编码为 CSI-u 修饰值（``1 + bits``，负数视作 0）。"""
    try:
        bits = int(bits)
    except (TypeError, ValueError):
        return 1
    return max(0, bits) + 1


def resolve_kitty_options(options: Any) -> int:
    """解析 ``kittyKeyboard`` 选项为「是否启用 + 标志掩码」。

    接受：
      - None/False：不启用 → 返回 -1；
      - True：启用（默认标志 ``disambiguateEscapeCodes``）→ 返回该掩码；
      - dict：``{"mode": "auto"|"enabled"|"disabled", "flags": [...]}``——
        ``disabled`` 返回 -1；``enabled``/``auto`` 返回 ``resolveFlags(flags)``
        （flags 缺省为 ``["disambiguateEscapeCodes"]``）。
    """
    if options is None or options is False:
        return -1
    if options is True:
        return kittyFlags["disambiguateEscapeCodes"]
    if isinstance(options, dict):
        mode = options.get("mode")
        if mode == "disabled":
            return -1
        flags = options.get("flags")
        if not flags:
            return kittyFlags["disambiguateEscapeCodes"]
        return resolveFlags(flags)
    return -1


def enable_sequence(flags: int = 0) -> str:
    """启用 kitty 键盘协议的终端序列（``CSI > flags u``）。

    ``flags`` 为位掩码（``resolveFlags`` 结果）；非法/非正数回退 1
    （``disambiguateEscapeCodes``）。
    """
    try:
        value = int(flags)
    except (TypeError, ValueError, OverflowError):
        value = 1
    if value <= 0:
        value = 1
    return f"\x1b[>{value}u"


def disable_sequence() -> str:
    """禁用（弹出）kitty 键盘协议的终端序列（``CSI < u``）。"""
    return "\x1b[<u"


__all__ = [
    "kittyFlags",
    "kittyModifiers",
    "resolveFlags",
    "encode_modifiers",
    "resolve_kitty_options",
    "enable_sequence",
    "disable_sequence",
]
