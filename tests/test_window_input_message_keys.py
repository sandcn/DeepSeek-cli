"""Windows GUI 键盘输入（``bash_opt`` ``op=key`` / ``op=type``）消息投递测试。

PostMessage 路径（``method='message'``）下，控件依赖 ``WM_CHAR`` 才能插入
文本：本测试锁定字符补发规则（Shift 改变字符、Ctrl/Alt/Meta 不产生字符、
Space/Tab 等键名需补发）与 ``op=type`` 的制表键语义，防止两条投递路径
（SendInput / PostMessage）对同一按键产生不一致结果。
"""

from __future__ import annotations

import pytest

from src.tools._screenshot import winapi
from src.tools._window_input import win as win_module
from src.tools._window_input.action import KeyAction, TextAction
from src.tools._window_input.geometry import WindowFrame
from src.tools._window_input.keys import parse_shortcut, shift_character
from src.tools._window_input.win import (
    WindowsInputBackend,
    _TargetWindow,
    _message_char_units,
)


# ── shift_character ─────────────────────────────────────

@pytest.mark.parametrize("char,expected", [
    ("a", "A"),
    ("z", "Z"),
    ("1", "!"),
    ("2", "@"),
    ("-", "_"),
    ("/", "?"),
    ("A", "A"),
    ("!", "!"),
    (" ", " "),
    ("中", "中"),
])
def test_shift_character(char: str, expected: str):
    assert shift_character(char) == expected


# ── WM_CHAR 补发规则 ────────────────────────────────────

@pytest.mark.parametrize("text,modifiers,expected", [
    ("a", (), [97]),
    ("a", ("shift",), [65]),
    ("A", ("shift",), [65]),
    ("1", ("shift",), [33]),
    ("!", ("shift",), [33]),
    ("space", (), [32]),
    ("tab", (), [9]),
    ("enter", (), []),
    ("backspace", (), []),
    ("up", (), []),
    ("a", ("ctrl",), []),
    ("a", ("alt",), []),
    ("a", ("meta",), []),
    ("space", ("ctrl",), []),
])
def test_message_char_units(text: str, modifiers: tuple, expected: list):
    units = _message_char_units(parse_shortcut(text), modifiers)
    assert units == expected


# ── PostMessage 消息序列 ────────────────────────────────

class _RecordingDriver:
    def __init__(self):
        self.calls: list[tuple] = []

    def post(self, handle, msg, wparam=0, lparam=0):
        self.calls.append((handle, msg, wparam, lparam))
        return True

    def messages(self) -> list[tuple]:
        """只保留 (消息, wParam) 便于断言。"""
        return [(msg, wparam) for _handle, msg, wparam, _lparam in self.calls]


@pytest.fixture()
def backend(monkeypatch):
    driver = _RecordingDriver()

    def _fake_resolve(key):
        table = {"a": (0x41, set()), "space": (0x20, set()),
                 "tab": (0x09, set()), "enter": (0x0D, set())}
        if key in table:
            return table[key]
        raise AssertionError(f"测试未覆盖的键: {key}")

    monkeypatch.setattr(win_module, "resolve_windows_vk", _fake_resolve)
    # op=type 的普通字符按物理键发送按下/弹起，需键位映射（跨平台可测）
    monkeypatch.setattr(win_module.winapi, "key_scan_code",
                        lambda char: (ord(char.upper()), 0))
    instance = WindowsInputBackend(locator=lambda pid: None, driver=driver)
    return instance, driver


def _target() -> _TargetWindow:
    return _TargetWindow(handle=100, pid=1, title="t",
                         frame=WindowFrame(0, 0, 100, 100))


def test_message_char_key_sends_char(backend):
    instance, driver = backend
    instance._key_message(_target(), KeyAction(shortcut=parse_shortcut("a")))
    assert driver.messages() == [
        (winapi.WM_KEYDOWN, 0x41),
        (winapi.WM_CHAR, 97),
        (winapi.WM_KEYUP, 0x41),
    ]


def test_message_shift_character_sends_shifted_char(backend):
    instance, driver = backend
    instance._key_message(_target(), KeyAction(shortcut=parse_shortcut("shift+a")))
    calls = driver.messages()
    assert (winapi.WM_CHAR, 65) in calls
    assert (winapi.WM_CHAR, 97) not in calls


def test_message_space_sends_wm_char(backend):
    instance, driver = backend
    instance._key_message(_target(), KeyAction(shortcut=parse_shortcut("space")))
    assert (winapi.WM_CHAR, 32) in driver.messages()


def test_message_control_combo_has_no_wm_char(backend):
    instance, driver = backend
    instance._key_message(_target(), KeyAction(shortcut=parse_shortcut("ctrl+a")))
    messages = driver.messages()
    assert (winapi.WM_CHAR, 97) not in messages
    assert messages[0] == (winapi.WM_KEYDOWN, 0x11)   # VK_CONTROL
    assert messages[-1] == (winapi.WM_KEYUP, 0x11)


def test_type_message_translates_tab_to_key(backend):
    instance, driver = backend
    result = instance._type_message(_target(), TextAction(text="a\tb"))
    messages = driver.messages()
    assert result["characters"] == 3
    assert (winapi.WM_CHAR, 97) in messages
    assert (winapi.WM_CHAR, 0x09) in messages
    assert (winapi.WM_KEYDOWN, winapi.VK_TAB) in messages
    assert (winapi.WM_KEYUP, winapi.VK_TAB) in messages
    assert (winapi.WM_CHAR, 98) in messages


def test_type_message_newline_uses_return_key(backend):
    instance, driver = backend
    instance._type_message(_target(), TextAction(text="a\nb"))
    messages = driver.messages()
    assert (winapi.WM_KEYDOWN, winapi.VK_RETURN) in messages
    assert (winapi.WM_CHAR, 0x0D) in messages
