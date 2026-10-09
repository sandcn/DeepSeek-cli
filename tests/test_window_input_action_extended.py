"""窗口输入扩充动作测试：hover 悬停、move 相对移动、click 长按 / 间隔。

覆盖动作模型构建与校验（action.py）以及三个平台后端的分发与命令构造
（Windows 假驱动、X11 假 runner、macOS 假鼠标驱动）。
"""

from __future__ import annotations

import types

import pytest

from src.tools._window_input import macos as macos_module
from src.tools._window_input import win as win_module
from src.tools._window_input import x11 as x11_module
from src.tools._window_input.action import (
    DEFAULT_CLICK_INTERVAL,
    DEFAULT_HOVER_DWELL,
    HoverAction,
    MoveAction,
    build_action,
    describe_action,
)
from src.tools._window_input.geometry import WindowFrame
from src.tools._window_input.macos import MacOSInputBackend
from src.tools._window_input.result import ActionError, InputError
from src.tools._window_input.win import WindowsInputBackend
from src.tools._window_input.x11 import X11InputBackend


# ── 动作模型 ────────────────────────────────────────────

def test_build_hover_defaults_and_explicit():
    action = build_action("hover", {"x": 10, "y": 20})
    assert isinstance(action, HoverAction)
    assert action.dwell == pytest.approx(DEFAULT_HOVER_DWELL)
    assert (action.x, action.y) == (10, 20)
    action2 = build_action("hover", {"x": 1, "y": 2, "dwell": 2.5})
    assert action2.dwell == pytest.approx(2.5)


def test_build_hover_requires_point_and_rejects_bad_dwell():
    with pytest.raises(ActionError):
        build_action("hover", {})
    with pytest.raises(ActionError):
        build_action("hover", {"x": 1, "y": 2, "dwell": -1})
    with pytest.raises(ActionError):
        build_action("hover", {"x": 1, "y": 2, "dwell": 999})


def test_build_move_relative():
    action = build_action("move", {"dx": -5, "dy": 12})
    assert action.is_relative
    assert (action.dx, action.dy) == (-5, 12)
    assert action.x is None and action.y is None


def test_build_move_relative_requires_both_axes():
    with pytest.raises(ActionError) as excinfo:
        build_action("move", {"dx": 5})
    assert "dx" in str(excinfo.value)


def test_build_move_rejects_absolute_and_relative_together():
    with pytest.raises(ActionError) as excinfo:
        build_action("move", {"x": 1, "y": 2, "dx": 3, "dy": 4})
    assert "不能同时" in str(excinfo.value)


def test_build_move_relative_rejects_bool_and_too_large():
    with pytest.raises(ActionError):
        build_action("move", {"dx": True, "dy": 0})
    with pytest.raises(ActionError):
        build_action("move", {"dx": 10_000_000, "dy": 0})


def test_build_click_hold_and_interval():
    action = build_action("click", {"x": 1, "y": 2, "hold": 1.5, "interval": 0.2})
    assert action.hold == pytest.approx(1.5)
    assert action.interval == pytest.approx(0.2)
    default = build_action("click", {})
    assert default.hold == 0.0
    assert default.interval == pytest.approx(DEFAULT_CLICK_INTERVAL)


def test_build_click_rejects_bad_hold_and_interval():
    with pytest.raises(ActionError):
        build_action("click", {"hold": -1})
    with pytest.raises(ActionError):
        build_action("click", {"hold": 999})
    with pytest.raises(ActionError):
        build_action("click", {"interval": -0.1})


def test_describe_action_extended():
    hover = describe_action(build_action("hover", {"x": 1, "y": 2, "dwell": 1.0}))
    assert hover["action"] == "hover" and hover["dwell"] == 1.0

    relative = describe_action(build_action("move", {"dx": 3, "dy": -4}))
    assert relative["relative"] == {"dx": 3, "dy": -4}

    click = describe_action(build_action("click", {"hold": 0.5, "interval": 0.3}))
    assert click["hold"] == 0.5 and click["interval"] == 0.3


def test_move_action_default_not_relative():
    assert MoveAction(x=1, y=2).is_relative is False


# ── Windows 后端 ────────────────────────────────────────

class _WinDriver:
    def __init__(self, cursor=(500, 400)):
        self.events = []
        self.foreground = True
        self._cursor = cursor

    def is_foreground(self, handle):
        return self.foreground

    def activate(self, handle):
        self.foreground = True
        return True

    def move_to(self, x, y):
        self.events.append(("move", x, y))

    def mouse_event(self, flags, data=0):
        self.events.append(("mouse", flags, data))

    def key_event(self, vk, *, key_up):
        self.events.append(("key", vk, key_up))

    def cursor_pos(self):
        return self._cursor

    def sleep(self, seconds):
        self.events.append(("sleep", seconds))

    def post(self, handle, msg, wparam=0, lparam=0):
        self.events.append(("post", handle, msg))
        return True

    def child_at(self, handle, x, y):
        return handle


def _win_backend(driver):
    frame = WindowFrame(screen_x=100, screen_y=200, width=800, height=600)

    def locator(_pid):
        return win_module._TargetWindow(handle=777, pid=1, title="App", frame=frame)

    return WindowsInputBackend(locator=locator, driver=driver)


def test_win_hover_moves_and_sleeps(monkeypatch):
    monkeypatch.setattr(win_module.winapi, "ensure_process_dpi_aware", lambda: True)
    driver = _WinDriver()
    result = _win_backend(driver).send(1, build_action("hover", {"x": 0, "y": 0, "dwell": 0.7}))
    assert ("move", 100, 200) in driver.events
    assert ("sleep", 0.7) in driver.events
    assert result.detail["hover"] is True


def test_win_move_relative_uses_cursor(monkeypatch):
    monkeypatch.setattr(win_module.winapi, "ensure_process_dpi_aware", lambda: True)
    driver = _WinDriver(cursor=(500, 400))
    result = _win_backend(driver).send(1, build_action("move", {"dx": 10, "dy": -20}))
    assert ("move", 510, 380) in driver.events
    assert result.detail["relative"] is True


def test_win_click_hold_and_interval(monkeypatch):
    monkeypatch.setattr(win_module.winapi, "ensure_process_dpi_aware", lambda: True)
    driver = _WinDriver()
    _win_backend(driver).send(1, build_action(
        "click", {"x": 0, "y": 0, "count": 2, "hold": 0.4, "interval": 0.1}))
    # 两次按下/弹起，其间有 hold 与 interval 的 sleep
    assert driver.events.count(("sleep", 0.4)) == 2
    assert ("sleep", 0.1) in driver.events
    down = win_module.winapi.MOUSEEVENTF_LEFTDOWN
    up = win_module.winapi.MOUSEEVENTF_LEFTUP
    assert driver.events.count(("mouse", down, 0)) == 2
    assert driver.events.count(("mouse", up, 0)) == 2


# ── X11 后端 ────────────────────────────────────────────

class _Runner:
    def __init__(self, returncode=0):
        self.commands = []
        self.returncode = returncode

    def __call__(self, command):
        self.commands.append(list(command))
        return types.SimpleNamespace(returncode=self.returncode, stdout="", stderr="")


@pytest.fixture(autouse=True)
def _fake_xdotool(monkeypatch):
    monkeypatch.setattr(x11_module, "require_xdotool", lambda: "/usr/bin/xdotool")


def _x11_backend(runner):
    target = x11_module._X11Target(
        window_id="1", pid=9, title="T",
        frame=WindowFrame(0, 0, 400, 300))
    return X11InputBackend(locator=lambda pid, window=None, runner=None: target,
                           runner=runner)


def test_x11_hover_command_chain():
    runner = _Runner()
    _x11_backend(runner).send(9, build_action("hover", {"x": 10, "y": 10, "dwell": 0.5}))
    command = runner.commands[-1]
    assert "mousemove" in command and "sleep" in command
    assert command[command.index("sleep") + 1] == "0.500"


def test_x11_relative_move_command():
    runner = _Runner()
    _x11_backend(runner).send(9, build_action("move", {"dx": 7, "dy": -3}))
    assert runner.commands[-1][:5] == [
        "/usr/bin/xdotool", "mousemove_relative", "--sync", "7", "-3"]


def test_x11_click_hold_uses_down_sleep_up():
    runner = _Runner()
    _x11_backend(runner).send(9, build_action("click", {"hold": 0.6}))
    command = runner.commands[-1]
    assert "mousedown" in command and "mouseup" in command
    assert command[command.index("sleep") + 1] == "0.600"


# ── macOS 后端 ─────────────────────────────────────────

class _MacMouse:
    name = "fake"

    def __init__(self, cursor=(10, 20)):
        self.calls = []
        self._cursor = cursor

    def move(self, x, y):
        self.calls.append(("move", x, y))

    def click(self, x, y, button, count, modifiers):
        self.calls.append(("click", x, y, button, count, tuple(modifiers)))

    def click_ex(self, x, y, button, count, hold, interval, modifiers):
        self.calls.append(("click_ex", x, y, button, count, hold, interval))

    def cursor_position(self):
        return self._cursor

    def drag(self, *args):
        self.calls.append(("drag",))

    def scroll(self, *args):
        self.calls.append(("scroll",))
        return None


def _mac_backend(mouse):
    def locator(_pid):
        return macos_module._MacTarget(
            number=1, pid=5, title="App",
            frame=WindowFrame(0, 0, 800, 600))
    return MacOSInputBackend(locator=locator, runner=_Runner(), mouse=mouse,
                             keyboard=object())


def test_macos_hover_moves_then_dwells(monkeypatch):
    mouse = _MacMouse()
    calls = []
    monkeypatch.setattr(macos_module.time, "sleep", lambda s: calls.append(s))
    _mac_backend(mouse).send(5, build_action("hover", {"x": 5, "y": 5, "dwell": 0.9}))
    assert mouse.calls[-1][0] == "move"
    assert calls == [0.9]


def test_macos_relative_move_uses_cursor():
    mouse = _MacMouse(cursor=(100, 100))
    _mac_backend(mouse).send(5, build_action("move", {"dx": 5, "dy": -5}))
    assert mouse.calls[-1] == ("move", 105, 95)


def test_macos_click_hold_uses_click_ex():
    mouse = _MacMouse()
    _mac_backend(mouse).send(5, build_action("click", {"hold": 0.3}))
    assert mouse.calls[-1][0] == "click_ex"
    assert mouse.calls[-1][5] == 0.3


class _NoExMouse(_MacMouse):
    """不支持长按的驱动（无 click_ex）。"""

    click_ex = None  # type: ignore[assignment]


def test_macos_click_hold_requires_capable_driver():
    with pytest.raises(InputError):
        _mac_backend(_NoExMouse()).send(5, build_action("click", {"hold": 0.3}))


# ── 公共入口的动作类型校验 ──────────────────────────────

def test_send_window_input_accepts_hover():
    from src.tools._window_input import register_backend, send_window_input
    from src.tools._window_input.result import InputResult

    seen = {}

    class _Backend:
        name = "fake-hover"

        def supports(self):
            return True

        def send(self, pid, action):
            seen["action"] = action.name
            return InputResult(action=action.name, backend=self.name,
                               window_pid=pid, window_title="W")

    undo = register_backend(_Backend(), prepend=True)
    try:
        result = send_window_input(1, build_action("hover", {"x": 1, "y": 2}))
    finally:
        undo()
    assert seen["action"] == "hover"
    assert result.action == "hover"
