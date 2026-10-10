"""游戏操作增强单元测试（相对位移事件 / 分阶段按键 / 长按 / hold_keys / release）。

覆盖：
  - 动作模型：move 的 relative_event / interval、click 的 phase 与 hold_keys、
    key 的 hold / interval / hold_keys、release 的 keys / buttons 解析与校验；
  - Windows 后端：相对位移事件的拆分与间隔、click 分阶段按下 / 弹起与状态记录、
    key 长按、连发间隔、hold_keys 的按下 / 释放包裹、release 兜底释放、
    输入会话（窗口定位缓存）；
  - X11 / macOS 后端：相对位移命令、click 分阶段、release、key 长按；
  - describe_action 对新字段的回显。
"""

from __future__ import annotations

import types

import pytest

from src.tools._window_input import macos as macos_module
from src.tools._window_input import win as win_module
from src.tools._window_input import x11 as x11_module
from src.tools._window_input.action import (
    ClickAction,
    KeyAction,
    MoveAction,
    ReleaseAction,
    build_action,
    describe_action,
)
from src.tools._window_input.geometry import WindowFrame
from src.tools._window_input.keys import parse_key_list
from src.tools._window_input.result import ActionError
from src.tools._window_input.win import WindowsInputBackend
from src.tools._window_input.x11 import X11InputBackend
from tests.test_window_input_win import FakeDriver


class GameFakeDriver(FakeDriver):
    """在 FakeDriver 基础上补上 ``relative_move``（相对位移事件）。"""

    def relative_move(self, dx, dy):
        self.events.append(("relative", dx, dy))


@pytest.fixture(autouse=True)
def _fake_winapi(monkeypatch):
    """屏蔽真实 Win32 调用（Cygwin 下避免触碰系统 API）。"""
    monkeypatch.setattr(win_module.winapi, "ensure_process_dpi_aware", lambda: True)
    monkeypatch.setattr(win_module.winapi, "is_window", lambda handle: True)
    monkeypatch.setattr(
        win_module.winapi, "key_scan_code",
        lambda char: (ord(char.upper()), 0) if len(char) == 1 else None,
    )


@pytest.fixture(autouse=True)
def _fake_xdotool(monkeypatch):
    monkeypatch.setattr(x11_module, "require_xdotool",
                        lambda: "/usr/bin/xdotool")


def _frame():
    return WindowFrame(screen_x=100, screen_y=200, width=800, height=600)


def _win_backend(driver, locator=None):
    def default_locator(_pid, window=None):
        return win_module._TargetWindow(handle=777, pid=1234, title="Game",
                                        frame=_frame())
    return WindowsInputBackend(locator=locator or default_locator, driver=driver)


def _events(driver, name):
    return [event for event in driver.events if event[0] == name]


# ── 动作模型 ────────────────────────────────────────────

def test_build_move_relative_event_parses_and_flags():
    action = build_action("move", {
        "dx": 10, "dy": -4, "relative_event": True, "steps": 4, "interval": 0.02,
    })
    assert isinstance(action, MoveAction)
    assert action.uses_relative_events is True
    assert action.steps == 4
    assert action.interval == 0.02


def test_build_move_relative_event_rejects_absolute_coordinates():
    with pytest.raises(ActionError) as excinfo:
        build_action("move", {"x": 1, "y": 2, "relative_event": True})
    assert "relative_event" in str(excinfo.value)


def test_build_move_interval_out_of_range():
    with pytest.raises(ActionError):
        build_action("move", {"dx": 1, "dy": 0, "interval": 99})


def test_build_click_phase_and_effective_count():
    action = build_action("click", {"phase": "down", "count": 5})
    assert isinstance(action, ClickAction)
    assert action.phase == "down"
    assert action.count == 5
    assert action.effective_count == 1
    assert build_action("click", {"count": 8}).effective_count == 8


def test_build_key_hold_and_interval():
    action = build_action("key", {"key": "w", "hold": 1.5, "interval": 0.02,
                                  "repeat": 3})
    assert isinstance(action, KeyAction)
    assert action.hold == 1.5
    assert action.interval == 0.02
    assert action.is_long_press is True


def test_build_key_hold_out_of_range():
    with pytest.raises(ActionError):
        build_action("key", {"key": "w", "hold": 999})


def test_build_hold_keys_accepts_string_and_list():
    action = build_action("click", {"hold_keys": ["w", "shift"]})
    assert action.hold_keys == ("w", "shift")
    action2 = build_action("click", {"hold_keys": "w+shift"})
    assert action2.hold_keys == ("w", "shift")


def test_build_hold_keys_rejects_unknown_key():
    with pytest.raises(ActionError):
        build_action("click", {"hold_keys": ["not-a-key"]})


def test_parse_key_list_limits_length():
    with pytest.raises(ActionError):
        parse_key_list(["w", "a"], maximum=1)


def test_build_release_variants():
    action = build_action("release", {})
    assert isinstance(action, ReleaseAction)
    assert action.keys == () and action.buttons == ()
    selected = build_action("release", {"keys": ["w"], "buttons": ["left"]})
    assert selected.keys == ("w",)
    assert selected.buttons == ("left",)


def test_build_release_rejects_bad_button():
    with pytest.raises(ActionError):
        build_action("release", {"buttons": ["wheel"]})


def test_describe_action_carries_game_fields():
    move = describe_action(build_action("move", {
        "dx": 3, "dy": 0, "relative_event": True, "interval": 0.01,
    }))
    assert move["relative_event"] is True
    click = describe_action(build_action("click", {"phase": "down"}))
    assert click["phase"] == "down"
    key = describe_action(build_action("key", {"key": "w", "hold": 0.5}))
    assert key["hold"] == 0.5
    release = describe_action(build_action("release", {}))
    assert release["all"] is True


# ── Windows 后端 ────────────────────────────────────────

def test_move_relative_event_splits_total_exactly():
    driver = GameFakeDriver()
    backend = _win_backend(driver)
    result = backend.send(1234, build_action("move", {
        "dx": 10, "dy": -4, "relative_event": True, "steps": 3,
    }))
    moves = _events(driver, "relative")
    assert len(moves) == 3
    assert sum(event[1] for event in moves) == 10
    assert sum(event[2] for event in moves) == -4
    assert result.to_dict()["relative_event"] is True


def test_move_relative_event_interval_sleeps_between_steps():
    driver = GameFakeDriver()
    backend = _win_backend(driver)
    backend.send(1234, build_action("move", {
        "dx": 40, "dy": 0, "relative_event": True, "steps": 4, "interval": 0.02,
    }))
    sleeps = _events(driver, "sleep")
    assert [event[1] for event in sleeps] == [0.02, 0.02, 0.02]


def test_click_phase_down_then_up_records_and_releases():
    driver = GameFakeDriver()
    backend = _win_backend(driver)
    backend.send(1234, build_action("click", {"phase": "down"}))
    assert _events(driver, "move") == []  # 未给坐标：不动光标
    assert _events(driver, "mouse") == [("mouse", 0x0002, 0)]  # LEFTDOWN
    assert backend._held_buttons == {"left"}
    backend.send(1234, build_action("click", {"phase": "up"}))
    assert _events(driver, "mouse")[-1] == ("mouse", 0x0004, 0)  # LEFTUP
    assert backend._held_buttons == set()


def test_key_hold_presses_sleeps_and_releases():
    driver = GameFakeDriver()
    backend = _win_backend(driver)
    backend.send(1234, build_action("key", {"key": "w", "hold": 0.4}))
    keys = _events(driver, "key")
    assert keys[0] == ("key", 0x57, False)
    assert keys[-1] == ("key", 0x57, True)
    assert ("sleep", 0.4) in driver.events


def test_key_interval_controls_repeat_gap():
    driver = GameFakeDriver()
    backend = _win_backend(driver)
    backend.send(1234, build_action("key", {
        "key": "f12", "repeat": 3, "interval": 0.02,
    }))
    sleeps = [event[1] for event in _events(driver, "sleep")]
    assert sleeps == [0.02, 0.02]


def test_hold_keys_wrap_action():
    driver = GameFakeDriver()
    backend = _win_backend(driver)
    backend.send(1234, build_action("click", {"hold_keys": ["w"]}))
    keys = _events(driver, "key")
    assert keys[0] == ("key", 0x57, False)
    assert keys[-1] == ("key", 0x57, True)
    mouse_index = driver.events.index(("mouse", 0x0002, 0))
    assert driver.events.index(keys[0]) < mouse_index < driver.events.index(keys[-1])


def test_release_sendinput_releases_all_remembered():
    driver = GameFakeDriver()
    backend = _win_backend(driver)
    backend.send(1234, build_action("key", {"key": "w", "phase": "down"}))
    backend.send(1234, build_action("click", {"phase": "down"}))
    driver.events.clear()
    result = backend.send(1234, build_action("release", {}))
    assert result.to_dict()["released_keys"] == ["w"]
    assert result.to_dict()["released_buttons"] == ["left"]
    assert ("key", 0x57, True) in driver.events
    assert ("mouse", 0x0004, 0) in driver.events


def test_release_sendinput_selected_target_only():
    driver = GameFakeDriver()
    backend = _win_backend(driver)
    backend.send(1234, build_action("key", {"key": "w", "phase": "down"}))
    driver.events.clear()
    result = backend.send(1234, build_action("release", {"keys": ["a"]}))
    assert result.to_dict()["scope"] == "selected"
    assert "a" in result.to_dict()["released_keys"]


def test_input_session_caches_window_locate():
    calls = {"count": 0}

    def locator(_pid, window=None):
        calls["count"] += 1
        return win_module._TargetWindow(handle=777, pid=1234, title="Game",
                                        frame=_frame())

    driver = GameFakeDriver()
    backend = _win_backend(driver, locator=locator)
    assert backend.begin_session(1234) is True
    backend.send(1234, build_action("click", {}))
    backend.send(1234, build_action("click", {}))
    backend.end_session()
    assert calls["count"] == 1


# ── X11 后端 ────────────────────────────────────────────

class _X11Runner:
    def __init__(self, returncode=0):
        self.commands = []
        self.returncode = returncode

    def __call__(self, command):
        self.commands.append(list(command))
        return types.SimpleNamespace(returncode=self.returncode, stdout="",
                                     stderr="")


def _x11_backend(runner):
    target = x11_module._X11Target(
        window_id="12345", pid=999, title="Game",
        frame=WindowFrame(screen_x=0, screen_y=0, width=400, height=300),
    )
    return X11InputBackend(locator=lambda pid, runner=None: target, runner=runner)


def test_x11_relative_event_command():
    runner = _X11Runner()
    _x11_backend(runner).send(999, build_action("move", {
        "dx": 12, "dy": -6, "relative_event": True, "steps": 2,
    }))
    relative = [cmd for cmd in runner.commands if "mousemove_relative" in cmd]
    assert len(relative) == 1  # 一条命令链里包含多步相对移动
    command = relative[0]
    assert command.count("mousemove_relative") == 2
    assert command[-2:] == ["6", "-3"]


def test_x11_click_phase_down_and_release():
    runner = _X11Runner()
    backend = _x11_backend(runner)
    backend.send(999, build_action("click", {"phase": "down"}))
    assert any("mousedown" in cmd for cmd in runner.commands)
    result = backend.send(999, build_action("release", {}))
    assert "left" in result.to_dict()["released_buttons"]
    assert any("mouseup" in cmd for cmd in runner.commands)


def test_x11_key_hold_uses_keydown_keyup():
    runner = _X11Runner()
    _x11_backend(runner).send(999, build_action("key", {"key": "w", "hold": 0.2}))
    assert any("keydown" in cmd for cmd in runner.commands)
    assert any("keyup" in cmd for cmd in runner.commands)


# ── macOS 后端 ──────────────────────────────────────────

class _MacMouse:
    def __init__(self):
        self.calls = []

    def move(self, x, y):
        self.calls.append(("move", x, y))

    def click(self, x, y, button, count, modifiers):
        self.calls.append(("click", x, y, button, count))

    def click_ex(self, x, y, button, count, hold, interval, modifiers):
        self.calls.append(("click_ex", x, y, button, count, hold, interval))

    def cursor_position(self):
        return (100, 100)

    def relative_move(self, dx, dy):
        self.calls.append(("relative", dx, dy))

    def button_down(self, x, y, button):
        self.calls.append(("down", x, y, button))

    def button_up(self, x, y, button):
        self.calls.append(("up", x, y, button))

    def drag(self, *args, **kwargs):
        self.calls.append(("drag", args))

    def scroll(self, x, y, direction, amount, modifiers):
        self.calls.append(("scroll", direction, amount))
        return None


class _MacKeyboard:
    def __init__(self):
        self.calls = []

    def key(self, shortcut, phase):
        self.calls.append(("key", shortcut.key, phase))

    def text(self, text):
        self.calls.append(("text", text))


def _mac_backend(mouse, keyboard):
    target = macos_module._MacTarget(
        number=1, pid=555, title="Game",
        frame=WindowFrame(screen_x=0, screen_y=0, width=400, height=300),
    )
    return macos_module.MacOSInputBackend(
        locator=lambda pid, window=None: target, runner=lambda cmd: None,
        mouse=mouse, keyboard=keyboard,
    )


def test_mac_relative_event_uses_driver_primitive():
    mouse, keyboard = _MacMouse(), _MacKeyboard()
    _mac_backend(mouse, keyboard).send(555, build_action("move", {
        "dx": 8, "dy": -2, "relative_event": True, "steps": 2,
    }))
    relative = [call for call in mouse.calls if call[0] == "relative"]
    assert relative == [("relative", 4, -1), ("relative", 4, -1)]


def test_mac_click_phase_down_records_button():
    mouse, keyboard = _MacMouse(), _MacKeyboard()
    backend = _mac_backend(mouse, keyboard)
    backend.send(555, build_action("click", {"phase": "down"}))
    assert ("down", 200, 150, "left") in mouse.calls
    assert backend._held_buttons == {"left"}


def test_mac_key_hold_uses_down_up():
    mouse, keyboard = _MacMouse(), _MacKeyboard()
    _mac_backend(mouse, keyboard).send(555, build_action("key", {
        "key": "w", "hold": 0.1,
    }))
    phases = [call[2] for call in keyboard.calls if call[0] == "key"]
    assert phases == ["down", "up"]


def test_mac_release_releases_keys_and_buttons():
    mouse, keyboard = _MacMouse(), _MacKeyboard()
    backend = _mac_backend(mouse, keyboard)
    backend.send(555, build_action("key", {"key": "w", "phase": "down"}))
    backend.send(555, build_action("click", {"phase": "down"}))
    result = backend.send(555, build_action("release", {}))
    assert "w" in result.to_dict()["released_keys"]
    assert "left" in result.to_dict()["released_buttons"]
    assert any(call[0] == "up" and call[3] == "left" for call in mouse.calls)
    assert ("key", "w", "up") in keyboard.calls


# ── 公共入口接受 release 动作 ────────────────────────────

def test_send_window_input_accepts_release():
    from src.tools._window_input import register_backend, send_window_input
    from src.tools._window_input.result import InputResult

    class _Backend:
        name = "fake"

        def supports(self):
            return True

        def send(self, pid, action):
            return InputResult(action=action.name, backend="fake", window_pid=pid,
                               window_title="", detail={})

    undo = register_backend(_Backend(), prepend=True)
    try:
        result = send_window_input(1, build_action("release", {}))
    finally:
        undo()
    assert result.action == "release"
