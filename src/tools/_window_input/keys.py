"""按键名规范化与跨平台键码映射（窗口输入注入共用）。

统一「规范化键名」是本模块的唯一约定，各平台后端据此查自己的键码表：

  - Windows：虚拟键码（``VK_*``，字符键由后端用 ``VkKeyScanW`` 解析）
  - X11：xdotool 的 keysym 名（``Return`` / ``Prior`` / ``ctrl`` …）
  - macOS：``osascript`` 的 key code（数字）

组合键语法：``ctrl+shift+s``、``alt+f4``、``ctrl+page_down``；也接受
``ctrl_c`` / ``ctrl-c`` 的紧凑写法（仅当整体不是已知键名时才拆分）。
"""

from __future__ import annotations

from dataclasses import dataclass

from .result import ActionError

#: 规范修饰键名（顺序即按下顺序）
MODIFIER_ORDER: tuple[str, ...] = ("ctrl", "alt", "shift", "meta")

#: 修饰键别名 → 规范名
MODIFIER_ALIASES: dict[str, str] = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "alt": "alt",
    "option": "alt",
    "opt": "alt",
    "shift": "shift",
    "meta": "meta",
    "win": "meta",
    "super": "meta",
    "cmd": "meta",
    "command": "meta",
}

#: 普通键别名 → 规范名
KEY_ALIASES: dict[str, str] = {
    "enter": "enter",
    "return": "enter",
    "cr": "enter",
    "escape": "escape",
    "esc": "escape",
    "tab": "tab",
    "space": "space",
    "spacebar": "space",
    "backspace": "backspace",
    "bksp": "backspace",
    "delete": "delete",
    "del": "delete",
    "insert": "insert",
    "ins": "insert",
    "home": "home",
    "end": "end",
    "page_up": "page_up",
    "pageup": "page_up",
    "pgup": "page_up",
    "prior": "page_up",
    "page_down": "page_down",
    "pagedown": "page_down",
    "pgdn": "page_down",
    "next": "page_down",
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
    "capslock": "caps_lock",
    "caps_lock": "caps_lock",
    "printscreen": "print_screen",
    "print_screen": "print_screen",
    "pause": "pause",
    "menu": "menu",
}

#: 功能键规范名（f1 - f24）
FUNCTION_KEYS: tuple[str, ...] = tuple(f"f{index}" for index in range(1, 25))
for _name in FUNCTION_KEYS:  # noqa: B007 - 仅填充别名表
    KEY_ALIASES[_name] = _name

#: 全部可识别的规范键名（修饰键除外）
KNOWN_KEYS: frozenset[str] = frozenset(KEY_ALIASES.values())

#: 未按 Shift 的字符键 → 按住 Shift 后产生的字符（US 布局）。
#: 合成键盘输入时用它把「Shift + 键」还原为实际字符（Windows 消息投递
#: 需要真正的字符，终端解析需要确定 Alt/Ctrl 组合的基字符）。
SHIFT_CHARACTER_MAP: dict[str, str] = {
    "1": "!", "2": "@", "3": "#", "4": "$", "5": "%",
    "6": "^", "7": "&", "8": "*", "9": "(", "0": ")",
    "-": "_", "=": "+", "[": "{", "]": "}", "\\": "|",
    ";": ":", "'": "\"", ",": "<", ".": ">", "/": "?", "`": "~",
    " ": " ",
}


def shift_character(char: str) -> str:
    """返回按住 Shift 时该字符键产生的字符（US 布局；无法确定时原样返回）。"""
    if len(char) != 1:
        return char
    if "a" <= char <= "z":
        return char.upper()
    return SHIFT_CHARACTER_MAP.get(char, char)


def utf16_units(char: str) -> list[int]:
    """把单个字符（可能是代理对）拆为 UTF-16 码元列表。

    合成键盘输入时用于把字符发送为系统可接受的 16 位码元序列
    （Windows SendInput / PostMessage、macOS CGEvent 共用）。
    """
    encoded = char.encode("utf-16-le")
    return [int.from_bytes(encoded[index:index + 2], "little")
            for index in range(0, len(encoded), 2)]


@dataclass(frozen=True)
class Shortcut:
    """一次按键（可带修饰键）。``key`` 为规范键名或单个字符。"""

    modifiers: tuple[str, ...]
    key: str

    @property
    def is_character(self) -> bool:
        """主键是否为单个可打印字符。"""
        return len(self.key) == 1

    def display(self) -> str:
        parts = list(self.modifiers) + [self.key]
        return "+".join(parts)


def classify_token(token: str) -> tuple[str, str]:
    """把单个 token 归类为 ``("modifier"|"key"|"char", 规范值)``。

    Raises:
        ActionError: token 为空、含不可打印字符或不是已知键名。
    """
    text = token.strip()
    if not text:
        raise ActionError("按键名不能为空")
    if len(text) == 1:
        if not text.isprintable():
            raise ActionError(f"按键名含不可打印字符: {text!r}")
        return "char", text
    normalized = text.lower().replace("-", "_")
    if normalized in MODIFIER_ALIASES:
        return "modifier", MODIFIER_ALIASES[normalized]
    if normalized in KEY_ALIASES:
        return "key", KEY_ALIASES[normalized]
    raise ActionError(
        f"未知按键: {token!r}。支持修饰键 ctrl/alt/shift/meta、编辑与导航键"
        f"（enter/escape/tab/space/backspace/delete/insert/home/end/page_up/"
        f"page_down/up/down/left/right）、功能键 f1-f24，或单个字符"
    )


def _split_compact(token: str) -> list[str]:
    """把 ``ctrl_c`` / ``ctrl-c`` 紧凑写法拆为 ``["ctrl", "c"]``。

    仅当整体不是已知键名（如 ``page_down``）且首段是修饰键时才拆分，
    否则原样返回。
    """
    normalized = token.strip().lower().replace("-", "_")
    if len(token.strip()) != 1 and normalized in KEY_ALIASES:
        return [token]
    parts = normalized.split("_")
    if len(parts) >= 2 and parts[0] in MODIFIER_ALIASES:
        rest = "_".join(parts[1:])
        if len(rest) == 1 or rest in KEY_ALIASES:
            return [parts[0], rest]
    return [token]


def parse_shortcut(text: str, *, allow_modifier_key: bool = False) -> Shortcut:
    """解析组合键文本为 :class:`Shortcut`。

    支持 ``ctrl+shift+s`` / ``alt+f4`` / ``enter`` / ``a``，以及
    ``ctrl_c`` / ``ctrl-c`` 紧凑写法。

    Args:
        text: 组合键文本。
        allow_modifier_key: 允许把单个修饰键当作主键（``key='ctrl'`` 表示
            按下/弹起 Ctrl 键本身，用于长按等场景）。默认关闭——普通组合键
            的最后一位必须是主键。

    Raises:
        ActionError: 文本为空、含空段、修饰键后缺少主键、或主键未知。
    """
    raw = str(text or "").strip()
    if not raw:
        raise ActionError("按键不能为空，示例: key='ctrl+s' / key='enter' / key='a'")
    tokens = [token for token in (part.strip() for part in raw.split("+")) if token]
    if not tokens:
        raise ActionError(f"按键格式非法: {text!r}")
    if len(tokens) == 1:
        tokens = _split_compact(tokens[0])
    modifiers: list[str] = []
    key: str | None = None
    for index, token in enumerate(tokens):
        kind, value = classify_token(token)
        if index < len(tokens) - 1:
            if kind != "modifier":
                raise ActionError(
                    f"组合键中除最后一位外都必须是修饰键（ctrl/alt/shift/meta），"
                    f"当前: {token!r}"
                )
            if value not in modifiers:
                modifiers.append(value)
            continue
        if kind == "modifier":
            if allow_modifier_key:
                key = value
                break
            raise ActionError(f"组合键缺少主键（最后一位不能是修饰键）: {text!r}")
        key = value
    if key is None:  # pragma: no cover - tokens 非空时不会发生
        raise ActionError(f"按键格式非法: {text!r}")
    ordered = tuple(name for name in MODIFIER_ORDER if name in modifiers)
    return Shortcut(modifiers=ordered, key=key)


def parse_modifiers(value) -> tuple[str, ...]:
    """解析 ``modifiers`` 参数（字符串 ``'ctrl+shift'`` 或字符串数组）。

    Raises:
        ActionError: 元素不是已知修饰键或类型非法。
    """
    if value is None:
        return ()
    if isinstance(value, str):
        items = [part for part in _split_any(value) if part]
    elif isinstance(value, (list, tuple, set, frozenset)):
        items = [str(item) for item in value if str(item).strip()]
    else:
        raise ActionError(
            f"modifiers 需为字符串（如 'ctrl+shift'）或字符串数组，当前: {value!r}"
        )
    names: list[str] = []
    for item in items:
        kind, name = classify_token(item)
        if kind != "modifier":
            raise ActionError(
                f"modifiers 只能是修饰键（ctrl/alt/shift/meta），当前: {item!r}"
            )
        if name not in names:
            names.append(name)
    return tuple(name for name in MODIFIER_ORDER if name in names)


def _split_any(text: str) -> list[str]:
    """按 ``+`` / ``,`` / 空白拆分为非空片段。"""
    buffer = ""
    parts: list[str] = []
    for char in text:
        if char in "+, \t":
            if buffer:
                parts.append(buffer)
            buffer = ""
        else:
            buffer += char
    if buffer:
        parts.append(buffer)
    return parts


# ── 平台键码表 ──────────────────────────────────────────

#: 规范键名 → Windows 虚拟键码（字符键由后端经 VkKeyScanW 解析）
WINDOWS_VK: dict[str, int] = {
    "enter": 0x0D,
    "escape": 0x1B,
    "tab": 0x09,
    "space": 0x20,
    "backspace": 0x08,
    "delete": 0x2E,
    "insert": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "page_up": 0x21,
    "page_down": 0x22,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
    "caps_lock": 0x14,
    "print_screen": 0x2C,
    "pause": 0x13,
    "menu": 0x5D,
    "ctrl": 0x11,
    "alt": 0x12,
    "shift": 0x10,
    "meta": 0x5B,
}
for _index, _name in enumerate(FUNCTION_KEYS, start=1):
    WINDOWS_VK[_name] = 0x6F + _index  # VK_F1 = 0x70

#: 规范键名 → xdotool keysym 名（字符键直接用字符本身）
X11_KEYSYM: dict[str, str] = {
    "enter": "Return",
    "escape": "Escape",
    "tab": "Tab",
    "space": "space",
    "backspace": "BackSpace",
    "delete": "Delete",
    "insert": "Insert",
    "home": "Home",
    "end": "End",
    "page_up": "Prior",
    "page_down": "Next",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "caps_lock": "Caps_Lock",
    "print_screen": "Print",
    "pause": "Pause",
    "menu": "Menu",
    "ctrl": "ctrl",
    "alt": "alt",
    "shift": "shift",
    "meta": "super",
}
for _index, _name in enumerate(FUNCTION_KEYS, start=1):
    X11_KEYSYM[_name] = f"F{_index}"

#: 规范键名 → macOS key code（osascript System Events）。
#: 仅收录 macOS 真实存在的键；未收录的规范键名（如 print_screen/pause/menu）
#: 在 macOS 上无对应虚拟键码，后端会给出明确错误。
MACOS_KEYCODE: dict[str, int] = {
    "enter": 36,
    "escape": 53,
    "tab": 48,
    "space": 49,
    "backspace": 51,
    "delete": 117,
    "insert": 114,
    "home": 115,
    "end": 119,
    "page_up": 116,
    "page_down": 121,
    "up": 126,
    "down": 125,
    "left": 123,
    "right": 124,
    "caps_lock": 57,
    "f1": 122,
    "f2": 120,
    "f3": 99,
    "f4": 118,
    "f5": 96,
    "f6": 97,
    "f7": 98,
    "f8": 100,
    "f9": 101,
    "f10": 109,
    "f11": 103,
    "f12": 111,
    "f13": 105,
    "f14": 107,
    "f15": 113,
    "f16": 106,
    "f17": 64,
    "f18": 79,
    "f19": 80,
    "f20": 90,
}

#: 字符 → macOS 虚拟键码（US ANSI 布局的物理键位）。
#: 用于 Quartz 分离按下/弹起时定位物理键；非 US 布局的字符可能无对应键位。
MACOS_CHAR_KEYCODE: dict[str, int] = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7,
    "c": 8, "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16,
    "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "=": 24,
    "9": 25, "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32,
    "[": 33, "i": 34, "p": 35, "l": 37, "j": 38, "'": 39, "k": 40, ";": 41,
    "\\": 42, ",": 43, "/": 44, "n": 45, "m": 46, ".": 47, "`": 50, " ": 49,
}

#: Shift 后的字符 → 其未加 Shift 的物理键字符（``SHIFT_CHARACTER_MAP`` 的逆向）
_UNSHIFTED_CHARACTER: dict[str, str] = {
    shifted: plain for plain, shifted in SHIFT_CHARACTER_MAP.items()
    if plain != shifted
}


def macos_keycode(key: str) -> tuple[int, bool] | None:
    """把规范键名或字符解析为 ``(macOS 虚拟键码, 是否需要 Shift)``。

    无法在当前键位表中定位（如非 US 布局字符、macOS 无对应键）时返回 None。
    """
    if len(key) == 1:
        if "A" <= key <= "Z":
            base, needs_shift = key.lower(), True
        elif key in _UNSHIFTED_CHARACTER:
            base, needs_shift = _UNSHIFTED_CHARACTER[key], True
        else:
            base, needs_shift = key, False
        code = MACOS_CHAR_KEYCODE.get(base.lower())
        return None if code is None else (code, needs_shift)
    code = MACOS_KEYCODE.get(key)
    return None if code is None else (code, False)

#: 修饰键 → macOS AppleScript 修饰符名
MACOS_MODIFIER_NAMES: dict[str, str] = {
    "ctrl": "control down",
    "alt": "option down",
    "shift": "shift down",
    "meta": "command down",
}


__all__ = [
    "FUNCTION_KEYS",
    "KEY_ALIASES",
    "KNOWN_KEYS",
    "MACOS_CHAR_KEYCODE",
    "MACOS_KEYCODE",
    "MACOS_MODIFIER_NAMES",
    "MODIFIER_ALIASES",
    "MODIFIER_ORDER",
    "SHIFT_CHARACTER_MAP",
    "Shortcut",
    "WINDOWS_VK",
    "X11_KEYSYM",
    "classify_token",
    "macos_keycode",
    "parse_modifiers",
    "parse_shortcut",
    "shift_character",
    "utf16_units",
]
