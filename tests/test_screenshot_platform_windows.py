"""X11 / macOS 后端的窗口选择与控制测试（纯逻辑，替换外部命令）。

覆盖：平台窗口条目 → 通用窗口描述、``list_windows`` 的主窗口标注、``capture``
按选择器挑窗口（含窗口号传参）、窗口控制的命令构造（xdotool / wmctrl、
AppleScript 语句）与权限 / 工具缺失时的报错。
"""

from __future__ import annotations

import pytest

from src.tools._screenshot import png
from src.tools._screenshot import macos as macos_mod
from src.tools._screenshot import x11 as x11_mod
from src.tools._screenshot.result import NoWindowError, ScreenshotError
from src.tools._screenshot.windows import (
    SelectorError,
    WindowControlRequest,
    WindowInfo,
)


class _Completed:
    """``subprocess.CompletedProcess`` 的最小替身。"""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _write_png(path, width=4, height=4):
    with open(path, "wb") as handle:
        handle.write(png.encode_png_rgb(width, height, b"\x00" * (width * height * 3)))


# ── X11 ──────────────────────────────────────────────────

def test_x11_to_window_info_maps_ids():
    window = x11_mod._X11Window(window_id="1234", pid=5, title="主窗口",
                                width=800, height=600, x=10, y=20,
                                class_name="google-chrome")
    info = x11_mod.to_window_info(window, order=2)
    assert info.handle == 1234
    assert info.pid == 5
    assert info.class_name == "google-chrome"
    assert info.order == 2
    assert (info.left, info.top) == (10, 20)
    assert x11_mod.window_id_int("0x4d2") == 1234


def test_x11_list_windows_marks_main(monkeypatch):
    windows = [
        x11_mod._X11Window(window_id="1", pid=5, title="主窗口", width=800, height=600),
        x11_mod._X11Window(window_id="2", pid=5, title="", width=200, height=100),
    ]
    monkeypatch.setattr(x11_mod, "find_process_windows", lambda pid: windows)
    infos = x11_mod.list_windows(5)
    assert [item.handle for item in infos] == [1, 2]
    assert [item.main for item in infos] == [True, False]
    monkeypatch.setattr(x11_mod, "find_process_windows", lambda pid: [])
    assert x11_mod.list_windows(5) == []


def test_x11_capture_selects_window_by_selector(monkeypatch, tmp_path):
    backend = x11_mod.X11Backend()
    windows = [
        x11_mod._X11Window(window_id="111", pid=5, title="主窗口", width=800, height=600),
        x11_mod._X11Window(window_id="222", pid=5, title="", width=200, height=100),
    ]
    monkeypatch.setattr(x11_mod.proctree, "collect_process_tree", lambda pid: [pid])
    monkeypatch.setattr(backend, "_find_windows", lambda pids: windows)
    grabbed = []
    monkeypatch.setattr(backend, "_grab",
                        lambda wid, path: (grabbed.append(wid), _write_png(path)))
    monkeypatch.setattr(backend, "_read_size", lambda path, target: (200, 100))

    result = backend.capture(5, str(tmp_path / "popup.png"), None, "popup")
    assert grabbed == ["222"]
    assert result.window_handle == 222
    assert result.windows_total == 2
    assert result.width == 200


def test_x11_capture_reports_selector_error(monkeypatch, tmp_path):
    backend = x11_mod.X11Backend()
    windows = [x11_mod._X11Window(window_id="111", pid=5, title="主窗口",
                                  width=800, height=600)]
    monkeypatch.setattr(x11_mod.proctree, "collect_process_tree", lambda pid: [pid])
    monkeypatch.setattr(backend, "_find_windows", lambda pids: windows)
    with pytest.raises(SelectorError):
        backend.capture(5, str(tmp_path / "x.png"), None, "title:不存在")


def test_x11_control_window_builds_tool_commands(monkeypatch):
    windows = [x11_mod._X11Window(window_id="1234", pid=5, title="主窗口",
                                  width=800, height=600)]
    monkeypatch.setattr(x11_mod, "find_process_windows", lambda pid: windows)
    monkeypatch.setattr(x11_mod.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(x11_mod.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(x11_mod, "_run", lambda command, timeout=None: None)
    calls = []

    def _runner(command):
        calls.append(command)
        return _Completed()

    detail = x11_mod.control_window(
        5, WindowControlRequest(action="resize", width=300, height=200),
        runner=_runner)
    assert ["xdotool", "windowsize", "1234", "300", "200"] in calls
    assert detail["window_action"] == "resize"
    assert detail["handle"] == 1234

    calls.clear()
    x11_mod.control_window(5, WindowControlRequest(action="activate"),
                           runner=_runner)
    assert ["wmctrl", "-i", "-a", "0x4d2"] in calls


def test_x11_control_window_reports_missing_tool(monkeypatch):
    windows = [x11_mod._X11Window(window_id="1234", pid=5, title="主窗口",
                                  width=800, height=600)]
    monkeypatch.setattr(x11_mod, "find_process_windows", lambda pid: windows)
    monkeypatch.setattr(x11_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(x11_mod.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(x11_mod, "_run", lambda command, timeout=None: None)
    with pytest.raises(ScreenshotError) as excinfo:
        x11_mod.control_window(5, WindowControlRequest(action="minimize"))
    assert "xdotool" in str(excinfo.value)


def test_x11_control_window_reports_no_window(monkeypatch):
    monkeypatch.setattr(x11_mod, "find_process_windows", lambda pid: [])
    with pytest.raises(NoWindowError):
        x11_mod.control_window(5, WindowControlRequest(action="activate"))


# ── macOS ────────────────────────────────────────────────

def test_macos_to_window_info_maps_number():
    window = macos_mod._MacWindow(number=42, pid=9, title="主窗口", width=800,
                                  height=600, x=1, y=2, class_name="chrome",
                                  foreground=True)
    info = macos_mod.to_window_info(window, order=1)
    assert info.handle == 42
    assert info.class_name == "chrome"
    assert info.foreground
    assert info.order == 1
    assert macos_mod.to_window_info(macos_mod._MacWindow(
        number=None, pid=9, title="", width=1, height=1)).handle == 0


def test_macos_list_windows_marks_main(monkeypatch):
    windows = [
        macos_mod._MacWindow(number=1, pid=9, title="主窗口", width=800, height=600),
        macos_mod._MacWindow(number=2, pid=9, title="", width=200, height=100),
    ]
    monkeypatch.setattr(macos_mod.proctree, "collect_process_tree", lambda pid: [pid])
    monkeypatch.setattr(macos_mod, "_process_windows", lambda pids: windows)
    infos = macos_mod.list_windows(9)
    assert [item.main for item in infos] == [True, False]


def test_macos_capture_selects_window_number(monkeypatch, tmp_path):
    backend = macos_mod.MacOSBackend()
    windows = [
        macos_mod._MacWindow(number=10, pid=9, title="主窗口", width=800, height=600),
        macos_mod._MacWindow(number=20, pid=9, title="", width=200, height=100),
    ]
    monkeypatch.setattr(macos_mod.proctree, "collect_process_tree", lambda pid: [pid])
    monkeypatch.setattr(macos_mod, "_process_windows", lambda pids: windows)
    commands = []
    monkeypatch.setattr(macos_mod, "_run_checked", lambda command: commands.append(command))

    def _size(path, target):
        _write_png(path, 200, 100)
        return 200, 100

    monkeypatch.setattr(macos_mod, "_read_size", _size)
    result = backend.capture(9, str(tmp_path / "m.png"), None, "#2")
    assert result.window_handle == 20
    assert "-l" in commands[0] and "20" in commands[0]
    assert result.width == 200


def test_macos_control_window_runs_applescript(monkeypatch):
    windows = [macos_mod._MacWindow(number=7, pid=9, title="主窗口",
                                    width=800, height=600)]
    monkeypatch.setattr(macos_mod.proctree, "collect_process_tree", lambda pid: [pid])
    monkeypatch.setattr(macos_mod, "_process_windows", lambda pids: windows)
    monkeypatch.setattr(macos_mod.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(macos_mod.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(macos_mod, "_run", lambda command, timeout=None: None)
    monkeypatch.setattr(macos_mod, "_quartz_bounds", lambda number: None)
    monkeypatch.setattr(macos_mod, "_osascript_bounds", lambda pid: None)
    calls = []

    def _runner(command):
        calls.append(command)
        return _Completed()

    detail = macos_mod.control_window(
        9, WindowControlRequest(action="fit", x=1, y=2, width=300, height=200),
        runner=_runner)
    assert detail["window_action"] == "fit"
    script = calls[0][-1]
    assert "set position of window 1 to {1, 2}" in script
    assert "set size of window 1 to {300, 200}" in script

    calls.clear()
    macos_mod.control_window(9, WindowControlRequest(action="minimize"),
                             runner=_runner)
    assert "AXMinimized" in calls[0][-1]


def test_macos_control_window_reports_failure(monkeypatch):
    windows = [macos_mod._MacWindow(number=7, pid=9, title="主窗口",
                                    width=800, height=600)]
    monkeypatch.setattr(macos_mod.proctree, "collect_process_tree", lambda pid: [pid])
    monkeypatch.setattr(macos_mod, "_process_windows", lambda pids: windows)
    monkeypatch.setattr(macos_mod.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(macos_mod.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(macos_mod, "_run", lambda command, timeout=None: None)
    monkeypatch.setattr(macos_mod, "_quartz_bounds", lambda number: None)
    monkeypatch.setattr(macos_mod, "_osascript_bounds", lambda pid: None)

    def _runner(command):
        return _Completed(returncode=1, stderr="not authorized")

    with pytest.raises(ScreenshotError) as excinfo:
        macos_mod.control_window(9, WindowControlRequest(action="close"),
                                 runner=_runner)
    assert "辅助功能" in str(excinfo.value)


def test_macos_control_window_requires_windows(monkeypatch):
    monkeypatch.setattr(macos_mod.proctree, "collect_process_tree", lambda pid: [pid])
    monkeypatch.setattr(macos_mod, "_process_windows", lambda pids: [])
    with pytest.raises(NoWindowError):
        macos_mod.control_window(9, WindowControlRequest(action="activate"))


def test_window_info_shared_type_used_by_platforms():
    """两平台都产出统一的 WindowInfo（选择规则跨平台一致）。"""
    assert isinstance(x11_mod.to_window_info(
        x11_mod._X11Window(window_id="1", pid=1, title="", width=1, height=1)), WindowInfo)
    assert isinstance(macos_mod.to_window_info(
        macos_mod._MacWindow(number=1, pid=1, title="", width=1, height=1)), WindowInfo)
