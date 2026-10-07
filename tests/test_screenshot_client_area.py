"""窗口 ``client_area`` 标记与属主激活（弹层可操作性预判）测试。

``op=windows`` 清单里的 ``client_area`` 字段让模型在投递前就能判断：该窗口
有没有可换算的客户区（``False`` 时不能用 ``method='message'`` 投递鼠标坐标）。
覆盖：字段默认值 / 序列化 / 摘要标记、Windows 枚举填充、探测不可用时的
``None`` 语义、``window_owner`` 读取与容错、工具窗口 activate 转向属主。
"""

from __future__ import annotations

import pytest

from src.tools._screenshot import win as win_mod
from src.tools._screenshot import winapi
from src.tools._screenshot.windows import (
    WindowControlRequest,
    WindowInfo,
    describe_windows,
    window_hint,
)


def _info(**overrides):
    base = dict(handle=0x10, pid=99, title="弹层", class_name="Chrome_WidgetWin_1",
                width=100, height=80)
    base.update(overrides)
    return WindowInfo(**base)


# ── WindowInfo 字段 ─────────────────────────────────────

def test_client_area_defaults_to_unknown():
    assert _info().client_area is None


def test_to_dict_exposes_client_area():
    assert _info(client_area=False).to_dict()["client_area"] is False
    assert _info(client_area=True).to_dict()["client_area"] is True
    assert _info().to_dict()["client_area"] is None


def test_summary_marks_only_missing_client_area():
    assert "no-client" in _info(client_area=False).summary(1)
    assert "no-client" not in _info(client_area=True).summary(1)
    assert "no-client" not in _info(client_area=None).summary(1)


def test_describe_and_hint_carry_client_area():
    windows = [_info(handle=0x1, client_area=False), _info(handle=0x2, client_area=True)]
    described = describe_windows(windows)
    assert [item["client_area"] for item in described] == [False, True]
    assert "no-client" in window_hint(windows)


# ── winapi 探测原语 ─────────────────────────────────────

def test_window_client_area_true_when_origin_and_size_available(monkeypatch):
    monkeypatch.setattr(winapi, "client_origin", lambda hwnd: (10, 20))
    monkeypatch.setattr(winapi, "client_size", lambda hwnd: (300, 200))
    assert winapi.window_client_area(0x1) is True


def test_window_client_area_false_when_origin_missing(monkeypatch):
    monkeypatch.setattr(winapi, "client_origin", lambda hwnd: None)
    assert winapi.window_client_area(0x1) is False


def test_window_client_area_false_when_size_empty(monkeypatch):
    monkeypatch.setattr(winapi, "client_origin", lambda hwnd: (0, 0))
    monkeypatch.setattr(winapi, "client_size", lambda hwnd: (0, 0))
    assert winapi.window_client_area(0x1) is False


def test_window_client_area_none_when_probe_unavailable(monkeypatch):
    """底层桩缺方法（AttributeError）时按「未知」返回 None，而不是崩溃。"""
    def _boom(hwnd):
        raise AttributeError("ClientToScreen")

    monkeypatch.setattr(winapi, "client_origin", _boom)
    assert winapi.window_client_area(0x1) is None


def test_window_owner_reads_owner(monkeypatch):
    class _User:
        def GetWindow(self, hwnd, command):
            return 0x40 if command == winapi.GW_OWNER else 0

    monkeypatch.setattr(winapi, "user32", lambda: _User())
    assert winapi.window_owner(0x1) == 0x40


def test_window_owner_returns_zero_on_failure(monkeypatch):
    class _User:
        def GetWindow(self, hwnd, command):
            raise OSError("boom")

    monkeypatch.setattr(winapi, "user32", lambda: _User())
    assert winapi.window_owner(0x1) == 0


# ── Windows 枚举填充 ────────────────────────────────────

class _FakeUser32:
    def IsWindowVisible(self, hwnd):
        return True

    def IsIconic(self, hwnd):
        return False


@pytest.fixture
def _fake_windows_api(monkeypatch):
    monkeypatch.setattr(win_mod.winapi, "user32", lambda: _FakeUser32())
    monkeypatch.setattr(win_mod.winapi, "enum_children_windows",
                        lambda: [0x1, 0x2])
    monkeypatch.setattr(win_mod.winapi, "window_pid", lambda hwnd: 99)
    monkeypatch.setattr(win_mod.winapi, "window_rect", lambda hwnd: (0, 0, 100, 80))
    monkeypatch.setattr(win_mod.winapi, "window_class",
                        lambda hwnd: "Chrome_WidgetWin_1")
    monkeypatch.setattr(win_mod.winapi, "window_text", lambda hwnd: "弹层")
    monkeypatch.setattr(win_mod.winapi, "window_is_toolwindow", lambda hwnd: True)
    monkeypatch.setattr(win_mod.winapi, "is_window_cloaked", lambda hwnd: False)
    monkeypatch.setattr(win_mod.winapi, "window_is_hung", lambda hwnd: False)
    monkeypatch.setattr(win_mod.winapi, "is_foreground", lambda hwnd: False)


def test_enumerate_fills_client_area(_fake_windows_api, monkeypatch):
    monkeypatch.setattr(win_mod.winapi, "window_client_area",
                        lambda hwnd: hwnd == 0x1)
    infos = win_mod.enumerate_window_infos({99})
    assert {item.handle: item.client_area for item in infos} == {0x1: True, 0x2: False}


# ── 工具窗口激活转向属主 ─────────────────────────────────

def test_control_activate_tool_window_targets_owner(monkeypatch):
    info = _info(handle=0x20, tool_window=True, client_area=False)
    monkeypatch.setattr(win_mod, "resolve_window_pids", lambda pid: {99})
    monkeypatch.setattr(win_mod, "enumerate_window_infos", lambda pids: [info])
    monkeypatch.setattr(win_mod.winapi, "is_window", lambda hwnd: True)
    monkeypatch.setattr(win_mod.winapi, "is_window_minimized", lambda hwnd: False)
    monkeypatch.setattr(win_mod.winapi, "is_window_visible", lambda hwnd: True)
    monkeypatch.setattr(win_mod.winapi, "window_owner", lambda hwnd: 0x40)
    monkeypatch.setattr(win_mod.winapi, "is_foreground", lambda hwnd: hwnd == 0x40)
    monkeypatch.setattr(win_mod.time, "sleep", lambda seconds: None)
    activated: list[int] = []
    monkeypatch.setattr(win_mod.winapi, "set_foreground",
                        lambda hwnd: activated.append(winapi.hwnd_value(hwnd)) or True)

    detail = win_mod.control_window(1234, WindowControlRequest(
        action="activate", selector="#1"))
    assert activated == [0x40]          # 只激活属主，不浪费在弹层自身
    assert detail["foreground"] is True
    assert detail["owner_handle"] == "0x40"
    assert "warning" not in detail


def test_control_activate_normal_window_uses_itself(monkeypatch):
    info = _info(handle=0x20, tool_window=False)
    monkeypatch.setattr(win_mod, "resolve_window_pids", lambda pid: {99})
    monkeypatch.setattr(win_mod, "enumerate_window_infos", lambda pids: [info])
    monkeypatch.setattr(win_mod.winapi, "is_window", lambda hwnd: True)
    monkeypatch.setattr(win_mod.winapi, "is_window_minimized", lambda hwnd: False)
    monkeypatch.setattr(win_mod.winapi, "is_window_visible", lambda hwnd: True)
    monkeypatch.setattr(win_mod.winapi, "window_owner", lambda hwnd: 0)
    monkeypatch.setattr(win_mod.winapi, "is_foreground", lambda hwnd: hwnd == 0x20)
    monkeypatch.setattr(win_mod.time, "sleep", lambda seconds: None)
    activated: list[int] = []
    monkeypatch.setattr(win_mod.winapi, "set_foreground",
                        lambda hwnd: activated.append(winapi.hwnd_value(hwnd)) or True)

    detail = win_mod.control_window(1234, WindowControlRequest(
        action="activate", selector="#1"))
    assert activated == [0x20]
    assert detail["foreground"] is True
