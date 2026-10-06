"""截图 / 控制入口的窗口选择转发，以及 Windows 窗口枚举的纯逻辑测试。

覆盖：``capture_process_window`` 按需透传 window / grid（保持只认 crop 的旧
后端可用）、``list_process_windows`` 与 ``control_process_window`` 的后端委托
与缺能力报错、Windows ``enumerate_window_infos`` 的 Z 序 / 前台标记 / 主窗口
标注、``select_window`` 兼容接口。
"""

from __future__ import annotations

import pytest

from src.tools._screenshot import (
    capture_process_window,
    control_process_window,
    list_process_windows,
    register_backend,
)
from src.tools._screenshot import png as png_mod
from src.tools._screenshot import win as win_mod
from src.tools._screenshot.result import (
    CaptureResult,
    NoWindowError,
    ScreenshotError,
)
from src.tools._screenshot.windows import (
    SelectorError,
    WindowControlRequest,
    WindowInfo,
)


class _RecordingBackend:
    """记录 capture 调用的替身后端（只接受 crop，验证旧后端仍可用）。"""

    name = "recording"

    def __init__(self, *, window_ok=False, control_ok=False):
        self.calls = []
        self._window_ok = window_ok
        self._control_ok = control_ok
        self.controls = []

    def supports(self):
        return True

    def capture(self, pid, path, crop=None, **kwargs):
        self.calls.append({"pid": pid, "crop": crop, **kwargs})
        with open(path, "wb") as handle:
            handle.write(png_mod.encode_png_rgb(2, 2, b"\x00" * 12))
        return CaptureResult(path=path, width=2, height=2, window_pid=pid,
                             window_title="", backend=self.name)

    def list_windows(self, pid):
        return [WindowInfo(handle=1, pid=pid, title="窗口", class_name="C",
                           width=10, height=10)]

    def control(self, pid, request):
        self.controls.append(request)
        return {"window_action": request.action}


def test_capture_forwards_window_and_grid_only_when_given(tmp_path):
    backend = _RecordingBackend(window_ok=True)
    undo = register_backend(backend, prepend=True)
    try:
        capture_process_window(1234, str(tmp_path / "a.png"))
        capture_process_window(1234, str(tmp_path / "b.png"), None, "#2", 20)
    finally:
        undo()
    assert backend.calls[0] == {"pid": 1234, "crop": None}
    assert backend.calls[1] == {"pid": 1234, "crop": None, "window": "#2", "grid": 20}


def test_capture_keeps_old_backend_without_window_signature(tmp_path):
    """只认 crop 的旧后端在未传 window/grid 时仍可工作（兼容性）。"""
    backend = _RecordingBackend()
    undo = register_backend(backend, prepend=True)
    try:
        result = capture_process_window(1234, str(tmp_path / "old.png"))
    finally:
        undo()
    assert result.width == 2
    assert backend.calls[0]["crop"] is None


def test_list_process_windows_delegates_to_backend():
    backend = _RecordingBackend()
    undo = register_backend(backend, prepend=True)
    try:
        infos = list_process_windows(4321)
    finally:
        undo()
    assert [item.title for item in infos] == ["窗口"]


def test_list_process_windows_without_capability_returns_empty():
    class _Plain:
        name = "plain"

        def supports(self):
            return True

        def capture(self, pid, path, crop=None):  # pragma: no cover - 不调用
            raise AssertionError("不应调用")

    undo = register_backend(_Plain(), prepend=True)
    try:
        assert list_process_windows(1234) == []
        assert list_process_windows(0) == []
    finally:
        undo()


def test_control_process_window_delegates_request():
    backend = _RecordingBackend()
    undo = register_backend(backend, prepend=True)
    try:
        request = WindowControlRequest(action="maximize", selector="#1")
        detail = control_process_window(555, request)
    finally:
        undo()
    assert detail == {"window_action": "maximize"}
    assert backend.controls[0] is request


def test_control_process_window_rejects_missing_capability():
    class _Plain:
        name = "plain"

        def supports(self):
            return True

        def capture(self, pid, path, crop=None):  # pragma: no cover - 不调用
            raise AssertionError("不应调用")

    undo = register_backend(_Plain(), prepend=True)
    try:
        with pytest.raises(ScreenshotError):
            control_process_window(1234, WindowControlRequest(action="minimize"))
    finally:
        undo()


def test_control_process_window_rejects_bad_pid():
    with pytest.raises(ScreenshotError):
        control_process_window(0, WindowControlRequest(action="minimize"))


# ── Windows 窗口枚举纯逻辑 ───────────────────────────────

class _FakeUser32:
    def __init__(self, minimized=()):
        self._minimized = set(minimized)

    def IsWindowVisible(self, hwnd):
        return True

    def IsIconic(self, hwnd):
        return hwnd in self._minimized


@pytest.fixture
def fake_windows_api(monkeypatch):
    """把 Win32 调用替换为内存假实现（不触碰真实系统）。"""
    handles = [0x101, 0x102, 0x103]
    texts = {0x101: "主窗口", 0x102: "", 0x103: "对话框"}
    pids = {0x101: 99, 0x102: 99, 0x103: 99}
    monkeypatch.setattr(win_mod.winapi, "user32", lambda: _FakeUser32())
    monkeypatch.setattr(win_mod.winapi, "enum_children_windows", lambda: list(handles))
    monkeypatch.setattr(win_mod.winapi, "window_pid", lambda hwnd: pids[hwnd])
    monkeypatch.setattr(win_mod.winapi, "window_rect", lambda hwnd: (0, 0, 400, 300))
    monkeypatch.setattr(win_mod.winapi, "window_class",
                        lambda hwnd: "Chrome_WidgetWin_1")
    monkeypatch.setattr(win_mod.winapi, "window_text", lambda hwnd: texts[hwnd])
    monkeypatch.setattr(win_mod.winapi, "window_is_toolwindow", lambda hwnd: False)
    monkeypatch.setattr(win_mod.winapi, "is_window_cloaked", lambda hwnd: False)
    monkeypatch.setattr(win_mod.winapi, "window_is_hung", lambda hwnd: False)
    monkeypatch.setattr(win_mod.winapi, "is_foreground", lambda hwnd: hwnd == 0x102)
    return handles


def test_enumerate_window_infos_marks_order_foreground_and_main(fake_windows_api):
    infos = win_mod.enumerate_window_infos({99})
    assert [item.handle for item in infos] == fake_windows_api
    assert [item.order for item in infos] == [0, 1, 2]
    assert [item.foreground for item in infos] == [False, True, False]
    # 主窗口：非工具 > 未最小化 > 有标题 > 面积大 → 两个有标题的窗口里取先者
    assert [item.main for item in infos] == [True, False, False]


def test_enumerate_window_infos_skips_shell_and_hung(monkeypatch, fake_windows_api):
    monkeypatch.setattr(win_mod.winapi, "window_class", lambda hwnd: "Progman")
    assert win_mod.enumerate_window_infos({99}) == []
    monkeypatch.setattr(win_mod.winapi, "window_class",
                        lambda hwnd: "Chrome_WidgetWin_1")
    monkeypatch.setattr(win_mod.winapi, "window_is_hung", lambda hwnd: hwnd == 0x101)
    remaining = win_mod.enumerate_window_infos({99})
    assert 0x101 not in {item.handle for item in remaining}


def test_select_window_alias_matches_windows_module(fake_windows_api):
    infos = win_mod.enumerate_window_infos({99})
    assert win_mod.select_window(infos).handle == 0x101
    assert win_mod.select_window([]) is None


def test_list_windows_uses_resolved_pids(monkeypatch, fake_windows_api):
    monkeypatch.setattr(win_mod, "resolve_window_pids", lambda pid: {99})
    infos = win_mod.list_windows(4321)
    assert [item.handle for item in infos] == fake_windows_api
    monkeypatch.setattr(win_mod, "resolve_window_pids", lambda pid: set())
    assert win_mod.list_windows(4321) == []


# ── Windows 窗口控制（假驱动） ───────────────────────────

def test_windows_control_window_dispatches_actions(monkeypatch, fake_windows_api):
    recorded = {}
    monkeypatch.setattr(win_mod, "resolve_window_pids", lambda pid: {99})
    monkeypatch.setattr(win_mod.winapi, "is_window", lambda hwnd: True)
    monkeypatch.setattr(win_mod.winapi, "is_window_minimized", lambda hwnd: False)
    monkeypatch.setattr(win_mod.winapi, "is_window_visible", lambda hwnd: True)
    monkeypatch.setattr(win_mod.winapi, "show_window",
                        lambda hwnd, cmd: recorded.setdefault("show", (hwnd, cmd)) or True)
    monkeypatch.setattr(win_mod.winapi, "set_window_pos",
                        lambda hwnd, x, y, w, h, flags: recorded.setdefault(
                            "pos", (x, y, w, h, flags)) or True)
    monkeypatch.setattr(win_mod.winapi, "close_window",
                        lambda hwnd: recorded.setdefault("close", hwnd) or True)
    monkeypatch.setattr(win_mod.winapi, "set_foreground",
                        lambda hwnd: recorded.setdefault("activate", hwnd) or True)
    monkeypatch.setattr(win_mod.time, "sleep", lambda seconds: None)

    detail = win_mod.control_window(1234, WindowControlRequest(action="maximize"))
    assert detail["window_action"] == "maximize"
    assert recorded["show"] == (0x101, win_mod.winapi.SW_MAXIMIZE)

    recorded.clear()
    win_mod.control_window(1234, WindowControlRequest(
        action="fit", selector="#3", x=1, y=2, width=300, height=200))
    assert recorded["pos"][:4] == (1, 2, 300, 200)

    recorded.clear()
    win_mod.control_window(1234, WindowControlRequest(action="close", selector="#2"))
    assert recorded["close"] == 0x102


def test_windows_control_window_reports_no_window(monkeypatch):
    monkeypatch.setattr(win_mod, "resolve_window_pids", lambda pid: {99})
    monkeypatch.setattr(win_mod, "enumerate_window_infos", lambda pids: [])
    with pytest.raises(NoWindowError):
        win_mod.control_window(1234, WindowControlRequest(action="activate"))


def test_windows_control_window_reports_selector_error(monkeypatch, fake_windows_api):
    monkeypatch.setattr(win_mod, "resolve_window_pids", lambda pid: {99})
    with pytest.raises(SelectorError):
        win_mod.control_window(1234, WindowControlRequest(
            action="activate", selector="title:不存在"))
