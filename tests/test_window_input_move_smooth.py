"""鼠标移动「平滑移动」增强的跨平台测试。

覆盖：

  - ``build_action("move", ...)`` 的 duration / steps 解析与默认值（默认一步直达）；
  - Windows 后端：平滑移动产生多个中间 ``move_to`` 事件（起点取当前光标）；
  - X11 后端：平滑移动构造 ``mousemove --sync`` 命令链；
  - macOS 后端：平滑移动分步调用鼠标驱动。
"""

from __future__ import annotations

import subprocess

import pytest

from src.tools._window_input import macos as macos_module
from src.tools._window_input import win as win_module
from src.tools._window_input import x11 as x11_module
from src.tools._window_input.action import (
    MAX_MOVE_DURATION,
    MAX_MOVE_STEPS,
    build_action,
    describe_action,
)
from src.tools._window_input.geometry import WindowFrame
from src.tools._window_input.result import ActionError


# ── 动作解析 ────────────────────────────────────────────

def test_move_defaults_are_one_step():
    action = build_action("move", {"x": 1, "y": 2})
    assert action.is_smooth is False
    assert action.steps == 1
    assert action.duration == 0.0


def test_move_duration_implies_smooth_default_steps():
    action = build_action("move", {"x": 1, "y": 2, "duration": 0.5})
    assert action.is_smooth is True
    assert action.steps == 20
    assert action.duration == 0.5


def test_move_explicit_steps_enables_smooth():
    action = build_action("move", {"dx": 5, "dy": -5, "steps": 8})
    assert action.is_smooth is True
    assert action.is_relative is True
    assert describe_action(action)["smooth"] == {"duration": 0.0, "steps": 8}


@pytest.mark.parametrize("params", [
    {"x": 1, "y": 2, "steps": 1},
    {"x": 1, "y": 2, "steps": MAX_MOVE_STEPS + 1},
    {"x": 1, "y": 2, "duration": MAX_MOVE_DURATION + 1},
    {"x": 1, "y": 2, "duration": -1},
])
def test_move_rejects_bad_smooth_params(params):
    with pytest.raises(ActionError):
        build_action("move", params)


# ── Windows ─────────────────────────────────────────────

class _FakeDriver:
    def __init__(self, cursor=(1000, 1000)):
        self.events = []
        self._cursor = cursor

    def is_foreground(self, handle):
        return True

    def activate(self, handle):
        return True

    def move_to(self, x, y):
        self.events.append(("move", x, y))

    def cursor_pos(self):
        return self._cursor

    def sleep(self, seconds):
        self.events.append(("sleep", seconds))


def _win_backend(driver):
    def locator(_pid):
        return win_module._TargetWindow(
            handle=1, pid=1234, title="t",
            frame=WindowFrame(100, 200, 800, 600))
    return win_module.WindowsInputBackend(locator=locator, driver=driver)


def test_win_smooth_move_interpolates(monkeypatch):
    monkeypatch.setattr(win_module.winapi, "ensure_process_dpi_aware", lambda: True)
    driver = _FakeDriver(cursor=(200, 400))
    result = _win_backend(driver).send(
        1234, build_action("move", {"x": 10, "y": 20, "steps": 4}))
    moves = [event for event in driver.events if event[0] == "move"]
    assert len(moves) == 4
    assert moves[-1] == ("move", 110, 220)     # 终点：frame 原点 + 窗口坐标
    assert result.detail["smooth"] == {"duration": 0.0, "steps": 4}


def test_win_non_smooth_move_single_event(monkeypatch):
    monkeypatch.setattr(win_module.winapi, "ensure_process_dpi_aware", lambda: True)
    driver = _FakeDriver()
    result = _win_backend(driver).send(1234, build_action("move", {"x": 10, "y": 20}))
    moves = [event for event in driver.events if event[0] == "move"]
    assert moves == [("move", 110, 220)]
    assert "smooth" not in result.detail


def test_win_smooth_move_relative_from_cursor(monkeypatch):
    monkeypatch.setattr(win_module.winapi, "ensure_process_dpi_aware", lambda: True)
    driver = _FakeDriver(cursor=(500, 500))
    _win_backend(driver).send(
        1234, build_action("move", {"dx": 40, "dy": 0, "steps": 4}))
    moves = [event for event in driver.events if event[0] == "move"]
    assert moves[0] == ("move", 510, 500)
    assert moves[-1] == ("move", 540, 500)


# ── X11 ─────────────────────────────────────────────────

class _FakeRunner:
    def __init__(self, location=(0, 0)):
        self.commands = []
        self.location = location

    def __call__(self, command):
        self.commands.append(list(command))
        if "getmouselocation" in command:
            stdout = f"X={self.location[0]}\nY={self.location[1]}\n"
            return subprocess.CompletedProcess(command, 0, stdout, "")
        return subprocess.CompletedProcess(command, 0, "", "")


def _x11_backend(runner):
    def locator(_pid, *_args, **_kwargs):
        return x11_module._X11Target(window_id="0x1", pid=1234, title="t",
                                     frame=WindowFrame(100, 200, 800, 600))
    return x11_module.X11InputBackend(locator=locator, runner=runner)


def test_x11_smooth_move_builds_command_chain(monkeypatch):
    monkeypatch.setattr(x11_module, "require_xdotool", lambda: "xdotool")
    runner = _FakeRunner(location=(0, 0))
    result = _x11_backend(runner).send(
        1234, build_action("move", {"x": 10, "y": 20, "steps": 4}))
    chains = [cmd for cmd in runner.commands if cmd[0] == "xdotool"
              and "mousemove" in cmd]
    assert chains, runner.commands
    chain = chains[-1]
    assert chain.count("mousemove") == 4
    assert chain[-2:] == ["--sync", chain[-1]] or "mousemove" in chain
    assert result.detail["smooth"]["steps"] == 4


def test_x11_non_smooth_move_single_command(monkeypatch):
    monkeypatch.setattr(x11_module, "require_xdotool", lambda: "xdotool")
    runner = _FakeRunner()
    _x11_backend(runner).send(1234, build_action("move", {"x": 10, "y": 20}))
    chains = [cmd for cmd in runner.commands if cmd[0] == "xdotool"
              and "mousemove" in cmd]
    assert chains[-1] == ["xdotool", "mousemove", "--sync", "110", "220"]
    assert sum(cmd.count("mousemove") for cmd in chains) == 1


# ── macOS ───────────────────────────────────────────────

class _FakeMouse:
    def __init__(self, cursor=(0, 0)):
        self.moves = []
        self._cursor = cursor

    def move(self, x, y):
        self.moves.append((x, y))

    def cursor_position(self):
        return self._cursor


def _mac_backend(mouse):
    def locator(_pid, *_args):
        return macos_module._MacTarget(number=1, pid=1234, title="t",
                                       frame=WindowFrame(100, 200, 800, 600))
    return macos_module.MacOSInputBackend(
        locator=locator, runner=lambda cmd: None, mouse=mouse,
        keyboard=object())


def test_macos_smooth_move_steps(monkeypatch):
    monkeypatch.setattr(macos_module.time, "sleep", lambda seconds: None)
    mouse = _FakeMouse(cursor=(0, 0))
    result = _mac_backend(mouse).send(
        1234, build_action("move", {"x": 10, "y": 20, "steps": 4}))
    assert len(mouse.moves) == 4
    assert mouse.moves[-1] == (110, 220)
    assert result.detail["smooth"]["steps"] == 4


def test_macos_non_smooth_move_single_call():
    mouse = _FakeMouse()
    _mac_backend(mouse).send(1234, build_action("move", {"x": 10, "y": 20}))
    assert mouse.moves == [(110, 220)]
