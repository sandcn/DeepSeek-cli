"""「按下 → 弹起」最小间隔测试（逐帧采样的程序不会吞掉点击 / 按键）。

背景（真实用例）：用 ``bash_opt`` 操作泰拉瑞亚（XNA 游戏）时，瞬时
``click``（down 与 up 之间没有间隔）被游戏静默吞掉——主菜单点了没反应，
必须 ``hold=0.3`` 才生效。原因：XNA / SDL 这类引擎只在帧边界读取输入状态，
down 与 up 落在同一帧时程序看到的一直是「没按过」。

修复后：所有后端在按下与弹起之间至少保持 ``MIN_PRESS_HOLD``（0.05s），
显式 ``hold`` 更大时以 ``hold`` 为准（长按语义不变）；结果里用
``press_hold`` 如实回报实际按住时长。本测试覆盖 Windows 的 SendInput /
PostMessage 两条路径，以及 ``effective_press_hold`` 的边界。
"""

from __future__ import annotations

import pytest

from src.tools._screenshot import winapi
from src.tools._window_input import win as win_module
from src.tools._window_input.action import (
    MIN_PRESS_HOLD,
    build_action,
    effective_press_hold,
)
from src.tools._window_input.geometry import WindowFrame
from src.tools._window_input.win import WindowsInputBackend


class _Driver:
    """记录事件（含 sleep）的假驱动。"""

    def __init__(self, *, foreground: bool = True):
        self.events: list[tuple] = []
        self.foreground = foreground

    def is_foreground(self, handle):
        return self.foreground

    def activate(self, handle):
        self.events.append(("activate", handle))
        return True

    def child_at(self, handle, screen_x, screen_y):
        return handle

    def move_to(self, x, y):
        self.events.append(("move", x, y))

    def mouse_event(self, flags, data=0):
        self.events.append(("mouse", flags, data))

    def key_event(self, vk, *, key_up):
        self.events.append(("key", vk, key_up))

    def unicode_event(self, code_unit, *, key_up):
        self.events.append(("unicode", code_unit, key_up))

    def post(self, handle, msg, wparam=0, lparam=0):
        self.events.append(("post", handle, msg, wparam, lparam))
        return True

    def client_origin(self, handle):
        return (0, 0)

    def sleep(self, seconds):
        self.events.append(("sleep", seconds))


@pytest.fixture(autouse=True)
def _no_real_dpi_call(monkeypatch):
    monkeypatch.setattr(win_module.winapi, "ensure_process_dpi_aware",
                        lambda: True)


def _backend(driver: _Driver) -> WindowsInputBackend:
    def locator(_pid):
        return win_module._TargetWindow(
            handle=1, pid=1, title="Game",
            frame=WindowFrame(100, 200, 800, 600),
        )

    return WindowsInputBackend(locator=locator, driver=driver)


def _sleeps(driver: _Driver) -> list[float]:
    return [event[1] for event in driver.events if event[0] == "sleep"]


# ── 纯函数：effective_press_hold ─────────────────────────

def test_effective_press_hold_never_below_minimum():
    assert effective_press_hold(0) == MIN_PRESS_HOLD
    assert effective_press_hold(None) == MIN_PRESS_HOLD
    assert effective_press_hold(-1) == MIN_PRESS_HOLD


def test_effective_press_hold_keeps_longer_explicit_hold():
    assert effective_press_hold(0.3) == 0.3
    assert effective_press_hold(30) == 30


# ── SendInput：click ─────────────────────────────────────

def test_click_holds_between_down_and_up():
    driver = _Driver()
    result = _backend(driver).send(1, build_action("click", {"x": 0, "y": 0}))
    down = winapi.MOUSEEVENTF_LEFTDOWN
    up = winapi.MOUSEEVENTF_LEFTUP
    index_down = driver.events.index(("mouse", down, 0))
    index_up = driver.events.index(("mouse", up, 0))
    assert index_down < index_up
    assert driver.events[index_down + 1] == ("sleep", MIN_PRESS_HOLD)
    assert driver.events[index_up - 1] == ("sleep", MIN_PRESS_HOLD)
    assert result.detail["press_hold"] == MIN_PRESS_HOLD


def test_click_explicit_hold_wins_over_minimum():
    driver = _Driver()
    result = _backend(driver).send(
        1, build_action("click", {"x": 0, "y": 0, "hold": 0.3}))
    assert 0.3 in _sleeps(driver)
    assert result.detail["press_hold"] == 0.3
    assert result.detail["hold"] == 0.3


def test_double_click_has_press_hold_on_each_click():
    driver = _Driver()
    _backend(driver).send(1, build_action("click", {"count": 2, "x": 0, "y": 0}))
    presses = [value for value in _sleeps(driver) if value == MIN_PRESS_HOLD]
    # 两次点击各一次按住 + 一次点击间隔（默认 interval 同为 0.05）
    assert len(presses) >= 2
    assert driver.events.count(("mouse", winapi.MOUSEEVENTF_LEFTDOWN, 0)) == 2
    assert driver.events.count(("mouse", winapi.MOUSEEVENTF_LEFTUP, 0)) == 2


# ── SendInput：key ───────────────────────────────────────

def test_key_press_holds_between_down_and_up(monkeypatch):
    monkeypatch.setattr(win_module, "resolve_windows_vk",
                        lambda key: (0x41, set()))
    driver = _Driver()
    result = _backend(driver).send(1, build_action("key", {"key": "a"}))
    assert driver.events == [
        ("key", 0x41, False),
        ("sleep", MIN_PRESS_HOLD),
        ("key", 0x41, True),
    ]
    assert result.detail["press_hold"] == MIN_PRESS_HOLD


def test_key_long_press_keeps_requested_hold(monkeypatch):
    monkeypatch.setattr(win_module, "resolve_windows_vk",
                        lambda key: (0x41, set()))
    driver = _Driver()
    _backend(driver).send(1, build_action("key", {"key": "a", "hold": 1.0}))
    assert 1.0 in _sleeps(driver)
    assert MIN_PRESS_HOLD not in _sleeps(driver)


def test_key_phase_down_and_up_do_not_add_hold(monkeypatch):
    monkeypatch.setattr(win_module, "resolve_windows_vk",
                        lambda key: (0x41, set()))
    driver = _Driver()
    _backend(driver).send(1, build_action("key", {"key": "a", "phase": "down"}))
    assert driver.events == [("key", 0x41, False)]
    assert not _sleeps(driver)


# ── PostMessage 路径 ─────────────────────────────────────

def test_click_message_path_also_holds():
    driver = _Driver(foreground=True)
    _backend(driver).send(
        1, build_action("click", {"x": 0, "y": 0, "method": "message"}))
    messages = [event[2] for event in driver.events if event[0] == "post"]
    assert winapi.WM_LBUTTONDOWN in messages
    assert winapi.WM_LBUTTONUP in messages
    assert ("sleep", MIN_PRESS_HOLD) in driver.events


def test_key_message_path_also_holds(monkeypatch):
    monkeypatch.setattr(win_module, "resolve_windows_vk",
                        lambda key: (0x41, set()))
    driver = _Driver()
    _backend(driver).send(
        1, build_action("key", {"key": "a", "method": "message"}))
    assert ("sleep", MIN_PRESS_HOLD) in driver.events


# ── type 里的回车 / 制表 ─────────────────────────────────

def test_enter_in_type_holds_minimum(monkeypatch):
    monkeypatch.setattr(win_module, "resolve_windows_vk",
                        lambda key: (0x41, set()))
    driver = _Driver()
    _backend(driver).send(1, build_action("type", {"text": "a\n"}))
    assert ("sleep", MIN_PRESS_HOLD) in driver.events
