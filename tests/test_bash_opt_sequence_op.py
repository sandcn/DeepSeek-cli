"""bash_opt ``op=sequence``（动作序列）测试。

覆盖：多步顺序执行（click / type / key / wait / screenshot / window）、每步
结果回报、``on_error`` 的 stop / continue 行为、步骤级 ``element`` 定位、
步骤级 ``settle`` / ``shot``、参数校验与无进程句柄时的失败记录。
"""

from __future__ import annotations

import json

from src.tools import bash_opt as bash_opt_module
from src.tools._screenshot.elements import ElementInfo
from src.tools._screenshot.result import CaptureResult
from src.tools._window_input.geometry import WindowFrame
from src.tools._window_input.result import InputResult, InputError
from src.tools.bash_opt import BashOptFunc


class _FakeAgent:
    def __init__(self, records: dict):
        self._background_tasks = records
        self._subagent_tasks = {}


def _record(pid=4321):
    return {"read_buffer": "", "status": "running", "done": False, "pid": pid,
            "task": None}


def _patch_send(monkeypatch, recorder: list, error_at=None):
    def _impl(pid, action):
        recorder.append(action)
        if error_at is not None and len(recorder) == error_at:
            raise InputError("注入失败（测试）")
        return InputResult(action=action.name, backend="windows", window_pid=pid,
                           window_title="App", detail={"delivery": "sendinput"})
    monkeypatch.setattr(bash_opt_module, "send_window_input", _impl)


def _patch_capture(monkeypatch, recorder: list):
    def _impl(pid, path, crop=None, **kwargs):
        recorder.append({"pid": pid, "path": path, "crop": crop, **kwargs})
        return CaptureResult(path=path, width=32, height=16, window_pid=pid,
                             window_title="App", backend="windows")
    monkeypatch.setattr(bash_opt_module, "capture_process_window", _impl)


def _patch_control(monkeypatch, recorder: list):
    def _impl(pid, request):
        recorder.append(request)
        return {"window_action": request.action, "after": {"x": 0, "y": 0}}
    monkeypatch.setattr(bash_opt_module, "control_process_window", _impl)


def _agent(func, rec=None):
    func.set_agent(_FakeAgent({"bg-1": rec or _record()}))
    return func


async def test_sequence_runs_steps_in_order(monkeypatch):
    sent: list = []
    captured: list = []
    _patch_send(monkeypatch, sent)
    _patch_capture(monkeypatch, captured)
    monkeypatch.setattr(BashOptFunc, "_SCREENSHOT_WAIT_SECONDS", 0.1)
    monkeypatch.setattr(BashOptFunc, "_SCREENSHOT_RETRY_INTERVAL", 0.01)
    func = _agent(BashOptFunc(task_id="bg-1", op="sequence", actions=[
        {"op": "click", "x": 10, "y": 20},
        {"op": "type", "text": "hello"},
        {"op": "key", "key": "enter"},
        {"op": "wait", "seconds": 0.01},
        {"op": "screenshot", "path": "step.png"},
    ]))
    payload = json.loads(await func.execute())
    assert payload["completed"] == 5 and payload["failed"] == 0
    assert [action.name for action in sent] == ["click", "type", "key"]
    assert [step["op"] for step in payload["steps"]] == [
        "click", "type", "key", "wait", "screenshot"]
    assert payload["steps"][1]["result"]["action"] == "type"
    assert captured[0]["path"].endswith("step.png")
    assert payload["stopped_early"] is False


async def test_sequence_stops_on_error_by_default(monkeypatch):
    sent: list = []
    _patch_send(monkeypatch, sent, error_at=2)
    func = _agent(BashOptFunc(task_id="bg-1", op="sequence", actions=[
        {"op": "click", "x": 1, "y": 1},
        {"op": "click", "x": 2, "y": 2},
        {"op": "key", "key": "enter"},
    ]))
    payload = json.loads(await func.execute())
    assert payload["completed"] == 1 and payload["failed"] == 1
    assert payload["stopped_early"] is True
    assert payload["steps"][1]["ok"] is False
    assert "注入失败" in payload["steps"][1]["error"]
    assert payload["steps"][1]["stopped"] is True
    # 第三步没有执行
    assert len(payload["steps"]) == 2
    assert [action.name for action in sent] == ["click", "click"]


async def test_sequence_continues_on_error_when_requested(monkeypatch):
    sent: list = []
    _patch_send(monkeypatch, sent, error_at=1)
    func = _agent(BashOptFunc(task_id="bg-1", op="sequence", on_error="continue",
                              actions=[
        {"op": "click", "x": 1, "y": 1},
        {"op": "key", "key": "enter"},
    ]))
    payload = json.loads(await func.execute())
    assert payload["completed"] == 1 and payload["failed"] == 1
    assert [step["ok"] for step in payload["steps"]] == [False, True]
    assert payload["on_error"] == "continue"


async def test_sequence_window_step_controls_window(monkeypatch):
    control: list = []
    _patch_control(monkeypatch, control)
    func = _agent(BashOptFunc(task_id="bg-1", op="sequence", actions=[
        {"op": "window", "window_action": "always_on_top"},
        {"op": "wait", "seconds": 0.01},
    ]))
    payload = json.loads(await func.execute())
    assert payload["completed"] == 2
    assert control[0].action == "always_on_top"
    assert payload["steps"][0]["window"]["window_action"] == "always_on_top"


async def test_sequence_step_element_click(monkeypatch):
    sent: list = []
    _patch_send(monkeypatch, sent)
    monkeypatch.setattr(bash_opt_module, "list_process_elements",
                        lambda pid, window=None: [
                            ElementInfo(handle=0x20, pid=99, class_name="Button",
                                        text="确定", left=200, top=150,
                                        width=80, height=30)])
    async def _frame(self, pid, window):
        return WindowFrame(100, 50, 800, 600)
    monkeypatch.setattr(BashOptFunc, "_window_frame_for", _frame)
    func = _agent(BashOptFunc(task_id="bg-1", op="sequence", actions=[
        {"op": "click", "element": "确定"},
    ]))
    payload = json.loads(await func.execute())
    assert payload["completed"] == 1
    assert (sent[0].x, sent[0].y) == (140, 115)
    assert payload["steps"][0]["element"]["label"] == "确定"


async def test_sequence_step_settle_and_shot(monkeypatch):
    sent: list = []
    captured: list = []
    _patch_send(monkeypatch, sent)
    _patch_capture(monkeypatch, captured)
    monkeypatch.setattr(BashOptFunc, "_SCREENSHOT_WAIT_SECONDS", 0.1)
    monkeypatch.setattr(BashOptFunc, "_SCREENSHOT_RETRY_INTERVAL", 0.01)
    func = _agent(BashOptFunc(task_id="bg-1", op="sequence", actions=[
        {"op": "click", "x": 1, "y": 1, "settle": 0.01, "shot": "after-click.png"},
    ]))
    payload = json.loads(await func.execute())
    assert payload["steps"][0]["screenshot"]["path"].endswith("after-click.png")
    assert captured[-1]["path"].endswith("after-click.png")


async def test_sequence_step_supports_wait_for_and_diff(monkeypatch, tmp_path):
    from src.tools._screenshot import png

    sent: list = []
    _patch_send(monkeypatch, sent)
    state = {"n": 0}
    colors = [(0, 0, 255, 255), (255, 0, 0, 255)]

    async def _temps(self, pid, window):
        index = min(state["n"], len(colors) - 1)
        state["n"] += 1
        path = tmp_path / f"seq-{state['n']}.png"
        path.write_bytes(png.encode_png_bgra(2, 2, bytes(colors[index]) * 4))
        return str(path)

    monkeypatch.setattr(BashOptFunc, "_temp_screenshot", _temps)
    monkeypatch.setattr(BashOptFunc, "_WAIT_FOR_INTERVAL", 0.01)
    func = _agent(BashOptFunc(task_id="bg-1", op="sequence", actions=[
        {"op": "click", "x": 1, "y": 1, "wait_for": "change",
         "wait_timeout": 0.5, "diff": True},
    ]))
    payload = json.loads(await func.execute())
    step = payload["steps"][0]
    assert step["ok"] is True
    assert step["wait_for"]["satisfied"] is True
    assert step["diff"]["changed"] is True


async def test_sequence_step_rejects_bad_wait_for(monkeypatch):
    sent: list = []
    _patch_send(monkeypatch, sent)
    func = _agent(BashOptFunc(task_id="bg-1", op="sequence", actions=[
        {"op": "click", "x": 1, "y": 1, "wait_for": "whenever"},
    ]))
    payload = json.loads(await func.execute())
    assert payload["failed"] == 1
    assert "wait_for 取值非法" in payload["steps"][0]["error"]


async def test_sequence_reports_invalid_actions_argument():
    func = _agent(BashOptFunc(task_id="bg-1", op="sequence", actions="click"))
    assert "sequence 参数非法" in await func.execute()
    func = _agent(BashOptFunc(task_id="bg-1", op="sequence", actions=[
        {"op": "click", "x": 1, "y": 1}], on_error="explode"))
    assert "on_error 取值非法" in await func.execute()


async def test_sequence_without_pid_fails_action_step(monkeypatch):
    sent: list = []
    _patch_send(monkeypatch, sent)
    rec = {"read_buffer": "", "status": "running", "done": False, "task": None}
    func = _agent(BashOptFunc(task_id="bg-1", op="sequence", actions=[
        {"op": "click", "x": 1, "y": 1}]), rec=rec)
    payload = json.loads(await func.execute())
    assert payload["failed"] == 1
    assert "尚无进程句柄" in payload["steps"][0]["error"]
