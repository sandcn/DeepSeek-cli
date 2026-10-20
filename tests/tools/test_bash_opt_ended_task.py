"""bash_opt：任务已结束时 screenshot / window / wait_window 的提示。

背景（实机复现）：后台任务结束后进程已退出，``op=screenshot`` 仍按 PID
枚举窗口，报「进程 N 及其子进程没有可截图的可见窗口（纯命令行进程无 GUI
窗口；窗口已最小化/被隐藏时也找不到；GUI 程序若经 App Execution Alias…）」，
把「任务已结束」误报成「窗口不可见」，模型据此反复 restore / 重试。

修复：三个依赖存活进程的 op 先检查 ``rec["done"]``，统一返回
「已结束（status，returncode=…），进程已退出」；整屏截图（screen）不依赖
任务窗口，任务结束后仍允许执行。
"""

from __future__ import annotations

import json

from src.tools import bash_opt as bash_opt_module
from src.tools._screenshot.monitors import Monitor
from src.tools._screenshot.result import CaptureResult
from src.tools.bash_opt import BashOptFunc


class _FakeAgent:
    def __init__(self, records: dict):
        self._background_tasks = records
        self._subagent_tasks = {}


def _record(done=True, status="completed", returncode=0, pid=4321):
    return {
        "read_buffer": "",
        "status": status,
        "done": done,
        "pid": pid,
        "returncode": returncode,
        "task": None,
    }


async def test_screenshot_rejects_finished_task(tmp_path):
    func = BashOptFunc(task_id="bg-1", op="screenshot",
                       path=str(tmp_path / "shot.png"))
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    result = await func.execute()
    assert "已结束" in result
    assert "returncode=0" in result
    assert "无法截图" in result
    assert not (tmp_path / "shot.png").exists()


async def test_window_rejects_finished_task():
    func = BashOptFunc(task_id="bg-1", op="window",
                       window_action="restore")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    result = await func.execute()
    assert "已结束" in result
    assert "无法控制窗口" in result


async def test_wait_window_rejects_finished_task():
    func = BashOptFunc(task_id="bg-1", op="wait_window")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    result = await func.execute()
    assert "已结束" in result
    assert "无法等待窗口" in result


async def test_screenshot_screen_allowed_after_task_finished(
        monkeypatch, tmp_path):
    """整屏截图不依赖任务窗口，任务结束后仍应执行（不被 done 拦截）。"""
    def _fake_capture(monitor, path, **kwargs):
        return CaptureResult(
            path=str(path), width=100, height=50,
            window_pid=0, window_title="", backend="fake",
            window_handle=0, window_selector="screen",
            window_x=0, window_y=0,
            window_rect={"x": 0, "y": 0, "width": 100, "height": 50},
        )

    monitor = Monitor(left=0, top=0, width=100, height=50, primary=True)
    monkeypatch.setattr(bash_opt_module, "list_monitors", lambda: [monitor])
    monkeypatch.setattr(bash_opt_module, "capture_screen", _fake_capture)
    func = BashOptFunc(task_id="bg-1", op="screenshot",
                       path=str(tmp_path / "screen.png"), screen=True)
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    payload = json.loads(await func.execute())
    assert payload["window_selector"] == "screen"
    assert payload["width"] == 100


async def test_screenshot_running_task_not_blocked_by_done_check(
        monkeypatch, tmp_path):
    """未结束任务不触发「已结束」提示（回归：不要把正常任务拦掉）。"""
    def _fake_capture(pid, path, crop=None, **kwargs):
        return CaptureResult(
            path=str(path), width=10, height=10, window_pid=pid,
            window_title="win", backend="fake", window_handle=1,
            window_selector="main", window_x=0, window_y=0,
            window_rect={"x": 0, "y": 0, "width": 10, "height": 10},
        )

    monkeypatch.setattr(bash_opt_module, "capture_process_window",
                        _fake_capture)
    func = BashOptFunc(task_id="bg-1", op="screenshot",
                       path=str(tmp_path / "run.png"))
    func.set_agent(_FakeAgent({"bg-1": _record(done=False, status="running")}))
    payload = json.loads(await func.execute())
    assert payload["window_title"] == "win"
