"""窗口几何与编号回填（实测驱动的修复回归）。

缺陷背景（Chrome 150% DPI 实机复现）：

  1. ``op=screenshot`` 返回的 ``window_summary`` 用**枚举序**编号（``order + 1``，
     实测 ``#33``），而 ``#N`` 选择器按**可操作窗口**（可见且未最小化）的 Z 序
     编号（实测同一窗口是 ``#1``）——模型照抄 ``#33`` 传给 ``window`` 会选不中。
  2. 产物尺寸（实测 1782x1281）与 ``op=windows`` 报的窗口外框（1800x1290）
     不一致，模型看不出产物左上角对应屏幕哪个点，无法把截图像素换算成屏幕坐标。

修复：新增 ``windows.indexed_summary`` / ``windows.window_geometry``；三个平台
截图后端统一回填 ``window_summary``（编号同 ``#N``）与 ``window_x`` /
``window_y`` / ``window_rect``；输入结果回填 ``window_frame``（命中窗口的截图
坐标系）；``op=windows`` 补充 ``selectable_total`` / ``windows_total``。

覆盖：摘要编号一致性、几何字段、三平台截图回填、InputResult 序列化、
bash_opt 的 op=windows 计数与 op=screenshot 提示。
"""

from __future__ import annotations

import json

import pytest

from src.tools import bash_opt as bash_opt_module
from src.tools._screenshot import macos as macos_mod
from src.tools._screenshot import png
from src.tools._screenshot import win as win_mod
from src.tools._screenshot import winapi
from src.tools._screenshot import x11 as x11_mod
from src.tools._screenshot.result import CaptureResult
from src.tools._screenshot.transform import CropRegion
from src.tools._screenshot.windows import (
    WindowInfo,
    describe_windows,
    indexed_summary,
    window_geometry,
)
from src.tools._window_input import build_action
from src.tools._window_input import win as input_win_module
from src.tools._window_input.geometry import WindowFrame
from src.tools._window_input.result import InputResult
from src.tools._window_input.win import WindowsInputBackend
from src.tools.bash_opt import BashOptFunc

from tests.test_window_input_win import FakeDriver


def _win(handle, *, pid=10, title="", class_name="Chrome_WidgetWin_1",
         width=800, height=600, left=0, top=0, tool=False, minimized=False,
         visible=True, foreground=False, order=0, main=False):
    return WindowInfo(
        handle=handle, pid=pid, title=title, class_name=class_name,
        width=width, height=height, left=left, top=top,
        tool_window=tool, minimized=minimized, visible=visible,
        foreground=foreground, order=order, main=main,
    )


# ── 摘要编号与 #N 选择器一致 ─────────────────────────────

def test_indexed_summary_uses_operable_z_index_not_enumeration_order():
    windows = [
        _win(0x1001, title="主窗口", order=0, foreground=True, main=True),
        _win(0x1002, title="", width=1920, height=997, order=31, visible=False),
        _win(0x1003, title="", width=320, height=240, order=32, tool=True),
    ]
    target = windows[2]
    # 无上下文的 summary 按枚举序编号（order+1 = 33），与 #N 选择器并不一致
    assert target.summary().startswith("#33")
    # indexed_summary 按可操作窗口 Z 序编号，与 '#2' 选择器一致
    assert indexed_summary(windows, target).startswith("#2")


def test_indexed_summary_marks_unselectable_window():
    hidden = _win(0x2002, title="", order=5, visible=False)
    assert indexed_summary([hidden], hidden).startswith("#-")


def test_describe_windows_and_indexed_summary_agree():
    windows = [
        _win(0x1001, title="主窗口", order=0, main=True),
        _win(0x1002, title="", order=7, visible=False),
        _win(0x1003, title="", order=8, tool=True),
    ]
    described = {item["handle"]: item["summary"] for item in describe_windows(windows)}
    for target in windows:
        assert described[target.handle] == indexed_summary(windows, target)


# ── window_geometry ──────────────────────────────────────

def test_window_geometry_reports_index_selectable_and_rect():
    windows = [
        _win(0x1001, title="主窗口", order=0, main=True, left=100, top=50,
             width=1280, height=900),
        _win(0x1002, title="", order=9, visible=False),
    ]
    geometry = window_geometry(windows, windows[0])
    assert geometry["z_index"] == 1
    assert geometry["handle"] == 0x1001
    assert geometry["handle_hex"] == "0x1001"
    assert geometry["selectable"] is True
    assert geometry["rect"] == {"x": 100, "y": 50, "width": 1280, "height": 900}
    hidden = window_geometry(windows, windows[1])
    assert hidden["z_index"] is None
    assert hidden["selectable"] is False


# ── Windows 截图回填 ─────────────────────────────────────

def _prepare_win_capture(monkeypatch, candidate, *, bounds):
    monkeypatch.setattr(winapi, "ensure_process_dpi_aware", lambda: True)
    monkeypatch.setattr(win_mod, "resolve_window_pids", lambda pid: {pid})
    monkeypatch.setattr(win_mod, "enumerate_candidates", lambda pids: [candidate])
    monkeypatch.setattr(
        win_mod, "capture_window_pixels",
        lambda c: (b"\x00" * (c.width * c.height * 4), c.width, c.height),
    )
    monkeypatch.setattr(winapi, "extended_frame_bounds", lambda hwnd: bounds)


def test_windows_capture_backfills_geometry_and_index_summary(monkeypatch, tmp_path):
    # 窗口外框 10,20,100x100；DWM 可见边界右移 8 像素（裁掉左侧黑边）
    candidate = _win(0x2222, title="", width=100, height=100, left=10, top=20,
                     tool=True)
    _prepare_win_capture(monkeypatch, candidate, bounds=(18, 20, 110, 120))

    result = win_mod.WindowsBackend().capture(42, str(tmp_path / "p.png"))

    assert (result.width, result.height) == (92, 100)
    assert (result.window_x, result.window_y) == (18, 20)
    assert result.window_rect == {"x": 10, "y": 20, "width": 100, "height": 100}
    assert result.window_summary.startswith("#1")


def test_windows_capture_origin_follows_crop(monkeypatch, tmp_path):
    candidate = _win(0x2222, title="", width=100, height=100, left=10, top=20,
                     tool=True)
    _prepare_win_capture(monkeypatch, candidate, bounds=(18, 20, 110, 120))

    result = win_mod.WindowsBackend().capture(
        42, str(tmp_path / "c.png"), CropRegion(5, 6, 30, 40))

    assert result.window_x == 18 + 5      # DWM 偏移 + crop 偏移
    assert result.window_y == 20 + 0 + 6
    assert (result.width, result.height) == (30, 40)


# ── X11 / macOS 截图回填 ─────────────────────────────────

def _write_png(path, width=4, height=4):
    with open(path, "wb") as handle:
        handle.write(png.encode_png_rgb(width, height, b"\x00" * (width * height * 3)))


def test_x11_capture_backfills_geometry(monkeypatch, tmp_path):
    backend = x11_mod.X11Backend()
    windows = [
        x11_mod._X11Window(window_id="111", pid=5, title="主窗口",
                           width=800, height=600, x=30, y=40),
        x11_mod._X11Window(window_id="222", pid=5, title="",
                           width=200, height=100, x=500, y=300),
    ]
    monkeypatch.setattr(x11_mod.proctree, "collect_process_tree", lambda pid: [pid])
    monkeypatch.setattr(backend, "_find_windows", lambda pids: windows)
    monkeypatch.setattr(backend, "_grab",
                        lambda wid, path: (wid, _write_png(path)))
    monkeypatch.setattr(backend, "_read_size", lambda path, target: (200, 100))

    result = backend.capture(5, str(tmp_path / "p.png"), None, "popup")

    assert (result.window_x, result.window_y) == (500, 300)
    assert result.window_rect == {"x": 500, "y": 300, "width": 200, "height": 100}
    assert result.window_summary.startswith("#2")


def test_macos_capture_backfills_geometry(monkeypatch, tmp_path):
    backend = macos_mod.MacOSBackend()
    windows = [
        macos_mod._MacWindow(number=10, pid=9, title="主窗口",
                             width=800, height=600, x=1, y=2),
        macos_mod._MacWindow(number=20, pid=9, title="",
                             width=200, height=100, x=50, y=60),
    ]
    monkeypatch.setattr(macos_mod.proctree, "collect_process_tree", lambda pid: [pid])
    monkeypatch.setattr(macos_mod, "_process_windows", lambda pids: windows)
    monkeypatch.setattr(macos_mod, "_run_checked", lambda command: None)

    def _size(path, target):
        _write_png(path, 200, 100)
        return 200, 100

    monkeypatch.setattr(macos_mod, "_read_size", _size)

    result = backend.capture(9, str(tmp_path / "m.png"), None, "#2")

    assert (result.window_x, result.window_y) == (50, 60)
    assert result.window_rect == {"x": 50, "y": 60, "width": 200, "height": 100}
    assert result.window_summary.startswith("#2")


# ── 输入结果回填 window_frame ────────────────────────────

def test_input_result_serializes_window_frame():
    payload = InputResult(
        action="click", backend="windows", window_pid=1, window_title="",
        window_frame={"screen_x": 5, "screen_y": 6, "width": 7, "height": 8},
    ).to_dict()
    assert payload["window_frame"] == {"screen_x": 5, "screen_y": 6,
                                       "width": 7, "height": 8}


def test_input_result_omits_window_frame_when_absent():
    payload = InputResult(action="click", backend="windows", window_pid=1,
                          window_title="").to_dict()
    assert "window_frame" not in payload


def _locator(pid, window=None):
    return input_win_module._TargetWindow(
        handle=0x20, pid=99, title="复杂操作测试台 v2 - Google Chrome",
        frame=WindowFrame(0, 0, 200, 100),
    )


def test_windows_backend_reports_window_frame(monkeypatch):
    monkeypatch.setattr(input_win_module.winapi, "ensure_process_dpi_aware",
                        lambda: True)
    backend = WindowsInputBackend(locator=_locator, driver=FakeDriver(foreground=True))
    result = backend.send(1234, build_action("click", {"x": 5, "y": 5}))
    assert result.window_frame == {"screen_x": 0, "screen_y": 0,
                                   "width": 200, "height": 100}


# ── bash_opt：op=windows 计数 / op=screenshot 提示 ────────

class _FakeAgent:
    def __init__(self, records: dict):
        self._background_tasks = records
        self._subagent_tasks = {}


def _record(pid=4321):
    return {"read_buffer": "", "status": "running", "done": False, "pid": pid,
            "task": None}


def _fast_retry(monkeypatch):
    monkeypatch.setattr(BashOptFunc, "_SCREENSHOT_WAIT_SECONDS", 0.2)
    monkeypatch.setattr(BashOptFunc, "_SCREENSHOT_RETRY_INTERVAL", 0.01)


async def test_windows_op_reports_selectable_and_total(monkeypatch):
    infos = [
        WindowInfo(handle=0x10, pid=99, title="主窗口", class_name="Chrome_Window",
                   width=800, height=600, order=0, main=True, foreground=True),
        WindowInfo(handle=0x11, pid=99, title="", class_name="Chrome_Window",
                   width=200, height=150, order=1),
        WindowInfo(handle=0x12, pid=99, title="", class_name="Chrome_WidgetWin_0",
                   width=1920, height=997, order=2, visible=False),
    ]
    monkeypatch.setattr(bash_opt_module, "list_process_windows", lambda pid: infos)
    func = BashOptFunc(task_id="bg-1", op="windows")
    func.set_agent(_FakeAgent({"bg-1": _record(pid=99)}))

    payload = json.loads(await func.execute())
    assert payload["total"] == 3
    assert payload["windows_total"] == 3
    assert payload["selectable_total"] == 2
    assert payload["windows"][0]["z_index"] == 1


async def test_screenshot_result_includes_window_geometry_and_hint(monkeypatch, tmp_path):
    _fast_retry(monkeypatch)

    def _impl(pid, path, crop=None, **kwargs):
        with open(path, "wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\n")
        return CaptureResult(
            path=path, width=92, height=100, window_pid=pid,
            window_title="Game", backend="windows",
            window_summary="#1 0x2222 「Game」 100x100",
            window_x=18, window_y=20,
            window_rect={"x": 10, "y": 20, "width": 100, "height": 100},
        )

    monkeypatch.setattr(bash_opt_module, "capture_process_window", _impl)
    func = BashOptFunc(task_id="bg-1", op="screenshot",
                       path=str(tmp_path / "s.png"))
    func.set_agent(_FakeAgent({"bg-1": _record(pid=3)}))

    payload = json.loads(await func.execute())
    assert payload["window_x"] == 18
    assert payload["window_y"] == 20
    assert payload["window_rect"]["width"] == 100
    assert payload["window_summary"].startswith("#1")
    assert "屏幕坐标" in payload["hint"]
