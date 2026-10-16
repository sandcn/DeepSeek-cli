"""窗口置顶与几何记忆测试。

覆盖：动作别名规范化、Windows 后端置顶 / 取消置顶调用、X11 后端置顶命令、
macOS 明确拒绝置顶、工具层 ``get_geometry`` / ``save_geometry`` /
``restore_geometry``（含未保存提示）。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.tools import bash_opt as bash_opt_module
from src.tools._screenshot import win as win_module
from src.tools._screenshot import x11 as x11_module
from src.tools._screenshot.result import ScreenshotError
from src.tools._screenshot.windows import (
    WindowControlRequest,
    WindowInfo,
    normalize_control_action,
)
from src.tools.bash_opt import BashOptFunc


class _FakeAgent:
    def __init__(self, records: dict):
        self._background_tasks = records
        self._subagent_tasks = {}


def _record(pid=4321):
    return {"read_buffer": "", "status": "running", "done": False, "pid": pid,
            "task": None}


def _info(handle=0x10, title="App"):
    return WindowInfo(handle=handle, pid=99, title=title, class_name="AppWindow",
                      width=800, height=600, left=100, top=50, order=0,
                      main=True, visible=True)


# ── 动作别名 ────────────────────────────────────────────

def test_normalize_control_action_aliases():
    assert normalize_control_action("topmost") == "always_on_top"
    assert normalize_control_action("pin") == "always_on_top"
    assert normalize_control_action("unpin") == "not_on_top"
    assert normalize_control_action("remember_geometry") == "save_geometry"
    assert normalize_control_action("reset_geometry") == "restore_geometry"
    assert normalize_control_action("max") == "maximize"
    assert normalize_control_action("whatever") is None
    assert normalize_control_action(None) is None


# ── Windows 后端 ────────────────────────────────────────

def _patch_windows_backend(monkeypatch, calls: list):
    monkeypatch.setattr(win_module, "resolve_window_pids", lambda pid: {99})
    monkeypatch.setattr(win_module, "enumerate_window_infos",
                        lambda pids: [_info()])
    monkeypatch.setattr(win_module.winapi, "set_window_topmost",
                        lambda hwnd, topmost=True: calls.append((hwnd, topmost)) or True)


def test_windows_backend_sets_topmost(monkeypatch):
    calls: list = []
    _patch_windows_backend(monkeypatch, calls)
    detail = win_module.control_window(4321, WindowControlRequest("always_on_top"))
    assert calls == [(0x10, True)]
    assert detail["window_action"] == "always_on_top"
    assert detail["window_title"] == "App"


def test_windows_backend_removes_topmost(monkeypatch):
    calls: list = []
    _patch_windows_backend(monkeypatch, calls)
    win_module.control_window(4321, WindowControlRequest("not_on_top"))
    assert calls == [(0x10, False)]


# ── X11 后端 ────────────────────────────────────────────

def _x11_runner(calls: list):
    def _run(command):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    return _run


def test_x11_apply_control_topmost(monkeypatch):
    calls: list = []
    monkeypatch.setattr(x11_module.shutil, "which", lambda name: "/usr/bin/" + name)
    x11_module._apply_control(_x11_runner(calls), "12345", "always_on_top",
                              WindowControlRequest("always_on_top"))
    assert calls[0][:4] == ["wmctrl", "-i", "-r", "0x3039"]
    assert "add,above" in calls[0]
    calls.clear()
    x11_module._apply_control(_x11_runner(calls), "12345", "not_on_top",
                              WindowControlRequest("not_on_top"))
    assert "remove,above" in calls[0]


# ── macOS 后端 ──────────────────────────────────────────

def test_macos_backend_rejects_topmost(monkeypatch):
    from src.tools._screenshot import macos as macos_module

    window = macos_module._MacWindow(number=1, pid=99, title="App",
                                     width=800, height=600, x=0, y=0)
    monkeypatch.setattr(macos_module.proctree, "collect_process_tree",
                        lambda pid: [99])
    monkeypatch.setattr(macos_module, "_process_windows", lambda pids: [window])
    with pytest.raises(ScreenshotError) as error:
        macos_module.control_window(99, WindowControlRequest("always_on_top"))
    assert "不支持" in str(error.value)


# ── 工具层几何记忆 ──────────────────────────────────────

def _patch_geometry(monkeypatch, control: list, infos=None):
    monkeypatch.setattr(bash_opt_module, "list_process_windows",
                        lambda pid: list(infos if infos is not None else [_info()]))

    def _impl(pid, request):
        control.append(request)
        return {"window_action": request.action, "after": {"x": 1}}
    monkeypatch.setattr(bash_opt_module, "control_process_window", _impl)


def _func(rec=None, **kwargs):
    func = BashOptFunc(task_id="bg-1", **kwargs)
    func.set_agent(_FakeAgent({"bg-1": rec or _record()}))
    return func


async def test_window_get_geometry(monkeypatch):
    control: list = []
    _patch_geometry(monkeypatch, control)
    payload = json.loads(await _func(
        op="window", window_action="get_geometry").execute())
    geometry = payload["geometry"]
    assert geometry["rect"] == {"x": 100, "y": 50, "width": 800, "height": 600}
    assert geometry["handle_hex"] == "0x10"
    assert control == []  # 只读，不触发窗口控制


async def test_window_save_and_restore_geometry(monkeypatch):
    control: list = []
    _patch_geometry(monkeypatch, control)
    rec = _record()
    func = _func(rec=rec, op="window", window_action="save_geometry")
    payload = json.loads(await func.execute())
    assert payload["saved"]["rect"]["width"] == 800
    assert rec["saved_geometry"]["window"] == "main"

    func = _func(rec=rec, op="window", window_action="restore_geometry")
    payload = json.loads(await func.execute())
    assert control[-1].action == "fit"
    assert control[-1].x == 100 and control[-1].height == 600
    assert payload["restored"]["rect"]["y"] == 50


async def test_window_restore_without_saved_geometry(monkeypatch):
    control: list = []
    _patch_geometry(monkeypatch, control)
    result = await _func(op="window", window_action="restore_geometry").execute()
    assert result.startswith("(窗口控制失败")
    assert "尚未保存窗口几何" in result


async def test_window_rejects_unknown_action(monkeypatch):
    control: list = []
    _patch_geometry(monkeypatch, control)
    result = await _func(op="window", window_action="teleport").execute()
    assert result.startswith("(窗口控制参数非法")
    assert "teleport" in result
