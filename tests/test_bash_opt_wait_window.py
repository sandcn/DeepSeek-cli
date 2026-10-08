"""bash_opt ``op=wait_window``（等待窗口出现）测试。

覆盖：立即命中、轮询后命中、超时提示（含当前窗口清单）、无进程句柄、
选择器一直匹配不到时的超时说明。
"""

from __future__ import annotations

import json

from src.tools import bash_opt as bash_opt_module
from src.tools._screenshot.windows import WindowInfo
from src.tools.bash_opt import BashOptFunc


class _FakeAgent:
    def __init__(self, records: dict):
        self._background_tasks = records
        self._subagent_tasks = {}


def _record(pid=4321):
    return {"read_buffer": "", "status": "running", "done": False, "pid": pid,
            "task": None}


def _window(title="主窗口", handle=0x10, order=0, main=True):
    return WindowInfo(handle=handle, pid=99, title=title, class_name="AppWindow",
                      width=800, height=600, order=order, main=main,
                      foreground=True)


def _fast(monkeypatch, interval=0.01):
    monkeypatch.setattr(BashOptFunc, "_WAIT_WINDOW_INTERVAL", interval)


async def test_wait_window_requires_process_handle():
    func = BashOptFunc(task_id="bg-1", op="wait_window")
    func.set_agent(_FakeAgent({"bg-1": {"read_buffer": "", "status": "running",
                                        "done": False}}))
    assert "尚无进程句柄" in await func.execute()


async def test_wait_window_returns_matching_window(monkeypatch):
    monkeypatch.setattr(bash_opt_module, "list_process_windows",
                        lambda pid: [_window()])
    _fast(monkeypatch)
    func = BashOptFunc(task_id="bg-1", op="wait_window", window="title:主")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    payload = json.loads(await func.execute())
    assert payload["found"] is True
    assert payload["window"]["title"] == "主窗口"
    assert payload["selector"] == "title:主"
    assert payload["attempts"] >= 1


async def test_wait_window_polls_until_window_appears(monkeypatch):
    calls = {"count": 0}

    def _impl(pid):
        calls["count"] += 1
        return [] if calls["count"] < 3 else [_window()]

    monkeypatch.setattr(bash_opt_module, "list_process_windows", _impl)
    _fast(monkeypatch)
    func = BashOptFunc(task_id="bg-1", op="wait_window", timeout=1.0)
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    payload = json.loads(await func.execute())
    assert payload["found"] is True and payload["attempts"] >= 3


async def test_wait_window_times_out_with_window_hint(monkeypatch):
    monkeypatch.setattr(bash_opt_module, "list_process_windows",
                        lambda pid: [_window(title="别的窗口")])
    _fast(monkeypatch)
    func = BashOptFunc(task_id="bg-1", op="wait_window", window="title:不存在的",
                       timeout=0.05)
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    result = await func.execute()
    assert result.startswith("(等待窗口超时")
    assert "别的窗口" in result
    assert "op=windows" in result


async def test_wait_window_times_out_without_any_window(monkeypatch):
    monkeypatch.setattr(bash_opt_module, "list_process_windows", lambda pid: [])
    _fast(monkeypatch)
    func = BashOptFunc(task_id="bg-1", op="wait_window", timeout=0.05)
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    result = await func.execute()
    assert result.startswith("(等待窗口超时")
    assert "无可见窗口" in result
