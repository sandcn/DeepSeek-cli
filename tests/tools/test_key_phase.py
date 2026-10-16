"""键盘按键「按下 / 弹起」分离的跨平台测试。

覆盖 ``action`` 的 ``phase`` 解析（press/down/up 与别名、非法值），以及
Windows（SendInput / PostMessage）、X11（xdotool keydown/keyup）、
macOS（Quartz 按键事件 / osascript 回退）三条后端各自的按下与弹起消息
序列——确保任一平台都不会把按下与弹起合并成一次动作或漏发其中一侧。
"""

from __future__ import annotations

import subprocess

import pytest

from src.tools._screenshot import winapi
from src.tools._window_input import macos as macos_module
from src.tools._window_input import win as win_module
from src.tools._window_input.action import (
    DEFAULT_KEY_PHASE,
    KEY_PHASES,
    TextAction,
    build_action,
)
from src.tools._window_input.geometry import WindowFrame
from src.tools._window_input.keys import parse_shortcut
from src.tools._window_input.macos import (
    AppleScriptKeyboardDriver,
    QuartzKeyboardDriver,
)
from src.tools._window_input.result import ActionError, InputError
from src.tools._window_input.win import (
    WindowsInputBackend,
    _TargetWindow,
)
from src.tools._window_input.x11 import X11InputBackend


# ── 动作构建：phase 解析 ────────────────────────────────

def test_default_phase_is_press():
    action = build_action("key", {"key": "ctrl+s"})
    assert action.phase == DEFAULT_KEY_PHASE == "press"
    assert KEY_PHASES == ("press", "down", "up")


@pytest.mark.parametrize("raw,expected", [
    ("press", "press"),
    ("down", "down"),
    ("keydown", "down"),
    ("hold", "down"),
    ("up", "up"),
    ("release", "up"),
    ("  UP  ", "up"),
])
def test_phase_aliases(raw: str, expected: str):
    assert build_action("key", {"key": "a", "phase": raw}).phase == expected


def test_invalid_phase_rejected():
    with pytest.raises(ActionError):
        build_action("key", {"key": "a", "phase": "sideways"})


def test_modifier_can_be_standalone_key():
    """key='ctrl' 可单独按下/弹起修饰键本身（长按场景）。"""
    action = build_action("key", {"key": "ctrl", "phase": "down"})
    assert action.shortcut.key == "ctrl"
    assert action.shortcut.modifiers == ()
    assert action.phase == "down"


def test_plain_shortcut_still_requires_main_key():
    """不带 allow_modifier_key 时（终端解析等）最后一位仍必须是主键。"""
    with pytest.raises(ActionError):
        parse_shortcut("ctrl+shift")


# ── Windows：SendInput 路径 ─────────────────────────────

class _KeyEventDriver:
    """记录 key_event(vk, key_up) 的 SendInput 假驱动。"""

    def __init__(self):
        self.events: list[tuple[int, bool]] = []

    def key_event(self, vk: int, *, key_up: bool) -> None:
        self.events.append((vk, key_up))


def _windows_backend(driver):
    return WindowsInputBackend(locator=lambda pid: None, driver=driver)


def _fake_resolve(key: str):
    table = {"a": (0x41, set()), "s": (0x53, set()), "ctrl": (0x11, set())}
    if key in table:
        return table[key]
    raise AssertionError(f"测试未覆盖的键: {key}")


@pytest.fixture()
def sendinput_backend(monkeypatch):
    monkeypatch.setattr(win_module, "resolve_windows_vk", _fake_resolve)
    driver = _KeyEventDriver()
    return _windows_backend(driver), driver


def test_sendinput_press_pairs_down_and_up(sendinput_backend):
    backend, driver = sendinput_backend
    backend._key_sendinput(build_action("key", {"key": "a"}))
    assert driver.events == [(0x41, False), (0x41, True)]


def test_sendinput_phase_down_only(sendinput_backend):
    backend, driver = sendinput_backend
    backend._key_sendinput(build_action("key", {"key": "a", "phase": "down"}))
    assert driver.events == [(0x41, False)]


def test_sendinput_phase_up_only(sendinput_backend):
    backend, driver = sendinput_backend
    backend._key_sendinput(build_action("key", {"key": "a", "phase": "up"}))
    assert driver.events == [(0x41, True)]


def test_sendinput_modifier_hold_and_release(sendinput_backend):
    """先 down 修饰键+主键、后 up 主键+修饰键，两次调用完成长按。"""
    backend, driver = sendinput_backend
    backend._key_sendinput(build_action("key", {"key": "ctrl+s", "phase": "down"}))
    backend._key_sendinput(build_action("key", {"key": "ctrl+s", "phase": "up"}))
    assert driver.events == [
        (0x11, False), (0x53, False),   # Ctrl down, S down
        (0x53, True), (0x11, True),     # S up, Ctrl up
    ]


# ── Windows：PostMessage 路径 ───────────────────────────

class _PostDriver:
    def __init__(self):
        self.calls: list[tuple[int, int, int, int]] = []

    def post(self, handle, msg, wparam=0, lparam=0):
        self.calls.append((handle, msg, wparam, lparam))
        return True

    def messages(self) -> list[tuple[int, int]]:
        return [(msg, wparam) for _h, msg, wparam, _lp in self.calls]


@pytest.fixture()
def message_backend(monkeypatch):
    monkeypatch.setattr(win_module, "resolve_windows_vk", _fake_resolve)
    driver = _PostDriver()
    return WindowsInputBackend(locator=lambda pid: None, driver=driver), driver


def _target() -> _TargetWindow:
    return _TargetWindow(handle=100, pid=1, title="t",
                         frame=WindowFrame(0, 0, 100, 100))


def test_message_press_pairs_down_and_up(message_backend):
    backend, driver = message_backend
    backend._key_message(_target(), build_action("key", {"key": "a"}))
    assert driver.messages() == [
        (winapi.WM_KEYDOWN, 0x41),
        (winapi.WM_CHAR, 97),
        (winapi.WM_KEYUP, 0x41),
    ]


def test_message_phase_down_has_no_keyup(message_backend):
    backend, driver = message_backend
    backend._key_message(_target(), build_action("key", {"key": "a", "phase": "down"}))
    assert driver.messages() == [
        (winapi.WM_KEYDOWN, 0x41),
        (winapi.WM_CHAR, 97),
    ]


def test_message_phase_up_has_no_keydown(message_backend):
    backend, driver = message_backend
    backend._key_message(_target(), build_action("key", {"key": "a", "phase": "up"}))
    assert driver.messages() == [(winapi.WM_KEYUP, 0x41)]


def test_message_alt_combination_uses_system_messages(message_backend):
    """Alt 组合键走 WM_SYSKEYDOWN/WM_SYSKEYUP（菜单加速键依赖系统消息）。"""
    backend, driver = message_backend
    backend._key_message(_target(), build_action("key", {"key": "alt+a"}))
    messages = driver.messages()
    assert (winapi.WM_SYSKEYDOWN, winapi.VK_MENU) in messages
    assert (winapi.WM_SYSKEYDOWN, 0x41) in messages
    assert (winapi.WM_SYSKEYUP, 0x41) in messages
    assert (winapi.WM_KEYDOWN, 0x41) not in messages


def test_message_standalone_modifier_down_and_up(message_backend):
    backend, driver = message_backend
    backend._key_message(_target(), build_action("key", {"key": "ctrl", "phase": "down"}))
    assert driver.messages() == [(winapi.WM_KEYDOWN, winapi.VK_CONTROL)]
    driver.calls.clear()
    backend._key_message(_target(), build_action("key", {"key": "ctrl", "phase": "up"}))
    assert driver.messages() == [(winapi.WM_KEYUP, winapi.VK_CONTROL)]


def test_type_message_pairs_keydown_char_keyup(monkeypatch, message_backend):
    """op=type 每个字符发送「按下 → 字符 → 弹起」（不只字符消息）。"""
    backend, driver = message_backend
    monkeypatch.setattr(win_module.winapi, "key_scan_code",
                        lambda char: (ord(char.upper()), 0))
    backend._type_message(_target(), TextAction(text="ab"))
    assert driver.messages() == [
        (winapi.WM_KEYDOWN, ord("A")),
        (winapi.WM_CHAR, ord("a")),
        (winapi.WM_KEYUP, ord("A")),
        (winapi.WM_KEYDOWN, ord("B")),
        (winapi.WM_CHAR, ord("b")),
        (winapi.WM_KEYUP, ord("B")),
    ]


# ── X11：xdotool keydown / keyup ────────────────────────

class _X11Runner:
    def __init__(self, code: int = 0):
        self.commands: list[list[str]] = []
        self._code = code

    def __call__(self, command):
        self.commands.append(list(command))
        return subprocess.CompletedProcess(command, self._code, "", "")


def _x11_backend(runner) -> X11InputBackend:
    return X11InputBackend(locator=lambda pid, runner=None: None, runner=runner)


def test_x11_press_sends_keydown_then_keyup():
    runner = _X11Runner()
    _x11_backend(runner)._key("xdotool", build_action("key", {"key": "ctrl+s"}))
    assert runner.commands == [
        ["xdotool", "keydown", "ctrl+s"],
        ["xdotool", "keyup", "ctrl+s"],
    ]


def test_x11_phase_down_only():
    runner = _X11Runner()
    _x11_backend(runner)._key(
        "xdotool", build_action("key", {"key": "shift", "phase": "down"}))
    assert runner.commands == [["xdotool", "keydown", "shift"]]


def test_x11_phase_up_only():
    runner = _X11Runner()
    _x11_backend(runner)._key(
        "xdotool", build_action("key", {"key": "ctrl", "phase": "up"}))
    assert runner.commands == [["xdotool", "keyup", "ctrl"]]


# ── macOS：Quartz 按键事件 ──────────────────────────────

class _FakeQuartz:
    kCGHIDEventTap = 0

    def __init__(self):
        self.events: list[dict] = []

    def CGEventCreateKeyboardEvent(self, source, code, key_down):
        event = {"code": code, "down": bool(key_down), "flags": 0}
        self.events.append(event)
        return event

    def CGEventSetFlags(self, event, flags):
        event["flags"] = flags

    def CGEventPost(self, tap, event):
        return None

    def CFRelease(self, event):
        return None

    def CGEventKeyboardSetUnicodeString(self, event, length, buffer):
        event["unicode"] = list(buffer)


def _quartz_driver() -> tuple[QuartzKeyboardDriver, _FakeQuartz]:
    fake = _FakeQuartz()
    driver = QuartzKeyboardDriver()
    driver._quartz = fake
    driver._loaded = True
    return driver, fake


def test_macos_quartz_press_pairs_events():
    driver, fake = _quartz_driver()
    driver.key(parse_shortcut("ctrl+s"), "press")
    assert [(e["code"], e["down"]) for e in fake.events] == [
        (59, True),    # control down
        (1, True),     # s down
        (1, False),    # s up
        (59, False),   # control up
    ]
    # 主键事件带 Ctrl 修饰标志
    assert fake.events[1]["flags"] == macos_module._QUARTZ_FLAGS["ctrl"]


def test_macos_quartz_phase_down_and_up():
    driver, fake = _quartz_driver()
    driver.key(parse_shortcut("a"), "down")
    assert [(e["code"], e["down"]) for e in fake.events] == [(0, True)]
    fake.events.clear()
    driver.key(parse_shortcut("a"), "up")
    assert [(e["code"], e["down"]) for e in fake.events] == [(0, False)]


def test_macos_quartz_shifted_character_sets_flag():
    driver, fake = _quartz_driver()
    driver.key(parse_shortcut("A"), "press")
    assert fake.events[0]["code"] == 0            # 物理键位 a
    assert fake.events[0]["flags"] == macos_module._QUARTZ_FLAGS["shift"]


# ── macOS：osascript 回退 ───────────────────────────────

class _ScriptRunner:
    def __init__(self):
        self.scripts: list[str] = []

    def __call__(self, command):
        self.scripts.append(command[-1])
        return subprocess.CompletedProcess(command, 0, "", "")


def test_applescript_modifier_down_statement():
    driver = AppleScriptKeyboardDriver(lambda command: None)
    statement = driver._phase_statement(
        parse_shortcut("ctrl", allow_modifier_key=True), "down")
    assert statement == "key down control"
    statement = driver._phase_statement(
        parse_shortcut("ctrl", allow_modifier_key=True), "up")
    assert statement == "key up control"


def test_applescript_plain_key_phase_unsupported():
    driver = AppleScriptKeyboardDriver(lambda command: None)
    assert driver._phase_statement(parse_shortcut("a"), "down") is None
    with pytest.raises(InputError):
        driver.key(parse_shortcut("a"), "down")


def test_applescript_modifier_phase_runs_multiline_script(monkeypatch):
    """多个修饰键的按下/弹起生成多行 AppleScript（tell 块形式）。"""
    monkeypatch.setattr(macos_module.shutil, "which", lambda name: "/usr/bin/osascript")
    runner = _ScriptRunner()
    driver = AppleScriptKeyboardDriver(runner)
    driver.key(parse_shortcut("ctrl+shift", allow_modifier_key=True), "up")
    assert runner.scripts == [
        'tell application "System Events"\n'
        "key up control\nkey up shift\n"
        "end tell"
    ]
