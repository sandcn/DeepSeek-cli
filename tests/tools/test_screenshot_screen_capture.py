"""整屏 / 多显示器截取 与 按控件区域截图 测试。

覆盖：显示器选择语义（monitors.resolve）、截图后端整屏接口（Windows 后端
用假像素替换）、bash_opt 的 screen / element / margin 截图参数。
"""

from __future__ import annotations

import json

import pytest

from src.tools import bash_opt as bash_opt_module
from src.tools._screenshot import png
from src.tools._screenshot.elements import ElementInfo
from src.tools._screenshot.monitors import (
    Monitor,
    MonitorError,
    resolve,
    union,
)
from src.tools._screenshot.result import CaptureResult
from src.tools.bash_opt import BashOptFunc


# ── 显示器选择 ──────────────────────────────────────────

def _monitors():
    return [Monitor(0, 0, 1920, 1080, primary=True),
            Monitor(1920, 0, 1280, 1024)]


def test_resolve_virtual_and_primary_and_index():
    monitors = _monitors()
    virtual = resolve(monitors, None)
    assert (virtual.left, virtual.top, virtual.width, virtual.height) == (0, 0, 3200, 1080)
    assert resolve(monitors, "primary").primary is True
    assert resolve(monitors, 2).left == 1920
    assert resolve(monitors, "1").width == 1920
    assert resolve(monitors, "all").width == 3200


def test_resolve_rejects_out_of_range():
    with pytest.raises(MonitorError):
        resolve(_monitors(), 3)
    with pytest.raises(MonitorError):
        resolve([], None)


def test_union_requires_monitors():
    with pytest.raises(MonitorError):
        union([])


# ── bash_opt: screen 截取 ───────────────────────────────

class _FakeAgent:
    def __init__(self, records: dict):
        self._background_tasks = records
        self._subagent_tasks = {}


def _record(pid=None):
    return {"read_buffer": "", "status": "running", "done": False, "pid": pid,
            "task": None}


def _patch_screen(monkeypatch, tmp_path, monitors=None):
    monitor_list = monitors or _monitors()
    monkeypatch.setattr(bash_opt_module, "list_monitors", lambda: list(monitor_list))

    def _capture(monitor, path, crop=None, grid=None):
        data = png.encode_png_rgb(
            max(monitor.width, 1), max(monitor.height, 1),
            bytes(max(monitor.width, 1) * max(monitor.height, 1) * 3))
        with open(path, "wb") as handle:
            handle.write(data)
        return CaptureResult(
            path=path, width=monitor.width, height=monitor.height,
            window_pid=0, window_title="", backend="windows",
            window_selector="screen",
            window_x=monitor.left, window_y=monitor.top,
            window_rect={"x": monitor.left, "y": monitor.top,
                         "width": monitor.width, "height": monitor.height})
    monkeypatch.setattr(bash_opt_module, "capture_screen", _capture)


async def test_screenshot_screen_virtual_desktop(monkeypatch, tmp_path):
    _patch_screen(monkeypatch, tmp_path)
    target = tmp_path / "screen.png"
    func = BashOptFunc(task_id="bg-1", op="screenshot", path=str(target),
                       screen=True)
    func.set_agent(_FakeAgent({"bg-1": _record(pid=None)}))
    payload = json.loads(await func.execute())
    assert payload["monitor"]["width"] == 3200
    assert payload["monitors_total"] == 2
    assert payload["width"] == 3200


async def test_screenshot_primary_monitor(monkeypatch, tmp_path):
    _patch_screen(monkeypatch, tmp_path)
    func = BashOptFunc(task_id="bg-1", op="screenshot",
                       path=str(tmp_path / "p.png"), screen="primary")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    payload = json.loads(await func.execute())
    assert payload["monitor"]["primary"] is True
    assert payload["monitor_index"] == 1


async def test_screenshot_monitor_index_out_of_range(monkeypatch, tmp_path):
    _patch_screen(monkeypatch, tmp_path)
    func = BashOptFunc(task_id="bg-1", op="screenshot",
                       path=str(tmp_path / "x.png"), screen=5)
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    result = await func.execute()
    assert "越界" in result


# ── bash_opt: element 区域截图 ──────────────────────────

def _patch_elements(monkeypatch, elements):
    monkeypatch.setattr(bash_opt_module, "list_process_elements",
                        lambda pid, window=None: list(elements))


async def _fake_frame(self, pid, window):
    from src.tools._window_input.geometry import WindowFrame
    return WindowFrame(100, 50, 800, 600)


async def test_screenshot_element_region(monkeypatch, tmp_path):
    element = ElementInfo(handle=1, pid=9, class_name="Button", text="确定",
                          left=200, top=120, width=60, height=30)
    _patch_elements(monkeypatch, [element])
    monkeypatch.setattr(BashOptFunc, "_window_frame_for", _fake_frame)
    captured = {}

    def _capture_window(pid, path, crop=None, window=None, grid=None):
        captured["crop"] = crop
        data = png.encode_png_rgb(800, 600, bytes(800 * 600 * 3))
        with open(path, "wb") as handle:
            handle.write(data)
        return CaptureResult(path=path, width=800, height=600, window_pid=pid,
                             window_title="App", backend="windows")
    monkeypatch.setattr(bash_opt_module, "capture_process_window", _capture_window)

    func = BashOptFunc(task_id="bg-1", op="screenshot",
                       path=str(tmp_path / "e.png"), element="确定", margin=5)
    func.set_agent(_FakeAgent({"bg-1": _record(pid=4321)}))
    payload = json.loads(await func.execute())
    crop = captured["crop"]
    # 控件窗口内坐标 (100,70)，外扩 5 → (95,65) 尺寸 70x40
    assert (crop.x, crop.y, crop.width, crop.height) == (95, 65, 70, 40)
    assert payload["element"]["label"] == "确定"
