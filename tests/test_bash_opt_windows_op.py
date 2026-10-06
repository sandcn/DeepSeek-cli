"""bash_opt 窗口相关能力测试（op=windows / op=window / window / shot / settle / grid）。

覆盖：schema 暴露（新 op 与新参数）、显示摘要、窗口清单输出（含无窗口与无进程
句柄路径）、窗口控制（参数透传、非法参数、选择器无匹配、超时）、输入 op 的
``window`` 选择器与 ``shot`` 自动截图（成功 / 失败 / 自动命名）、``settle``
解析、``screenshot`` 的 ``window`` / ``grid`` 透传。
"""

from __future__ import annotations

import json

import pytest

from src.tools import bash_opt as bash_opt_module
from src.tools._screenshot.result import CaptureResult, ScreenshotError
from src.tools._screenshot.windows import (
    SelectorError,
    WindowControlRequest,
    WindowInfo,
)
from src.tools._window_input.result import InputResult
from src.tools.bash_opt import BashOptFunc


class _FakeAgent:
    """最小 Agent 桩：只提供 bash 后台任务表。"""

    def __init__(self, records: dict):
        self._background_tasks = records
        self._subagent_tasks = {}


def _record(pid=4321, status="running"):
    return {
        "read_buffer": "",
        "status": status,
        "done": False,
        "pid": pid,
        "task": None,
    }


def _fast_retry(monkeypatch):
    monkeypatch.setattr(BashOptFunc, "_SCREENSHOT_WAIT_SECONDS", 0.2)
    monkeypatch.setattr(BashOptFunc, "_SCREENSHOT_RETRY_INTERVAL", 0.01)
    monkeypatch.setattr(BashOptFunc, "_DEFAULT_SHOT_SETTLE", 0.0)


def _fake_send(monkeypatch, recorder: dict, result=None, error=None):
    def _impl(pid, action):
        recorder["pid"] = pid
        recorder["action"] = action
        if error is not None:
            raise error
        return result or InputResult(
            action=action.name, backend="windows", window_pid=pid,
            window_title="Game", detail={"delivery": "sendinput"},
        )
    monkeypatch.setattr(bash_opt_module, "send_window_input", _impl)


def _fake_capture(monkeypatch, recorder: dict, *, error=None):
    def _impl(pid, path, crop=None, **kwargs):
        recorder["pid"] = pid
        recorder["path"] = path
        recorder["crop"] = crop
        recorder.update(kwargs)
        if error is not None:
            raise error
        with open(path, "wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\n")
        return CaptureResult(path=path, width=32, height=16, window_pid=pid,
                             window_title="Game", backend="windows")
    monkeypatch.setattr(bash_opt_module, "capture_process_window", _impl)


# ── schema / 显示 ────────────────────────────────────────

def test_schema_exposes_window_ops_and_parameters():
    params = BashOptFunc.to_tool_schema()["function"]["parameters"]
    enum = params["properties"]["op"]["enum"]
    assert "windows" in enum and "window" in enum
    for name in ("window", "grid", "shot", "settle", "window_action",
                 "width", "height"):
        assert name in params["properties"], name
    assert params["properties"]["window_action"]["enum"] == [
        "activate", "maximize", "minimize", "restore", "close",
        "move", "resize", "fit",
    ]
    assert params["required"] == ["task_id", "op"]


def test_schema_descriptions_mention_window_capabilities():
    schema = BashOptFunc.to_tool_schema()
    description = schema["function"]["description"]
    assert "windows" in description and "window_action" in description
    op_description = schema["function"]["parameters"]["properties"]["op"]["description"]
    assert "列出" in op_description
    assert "窗口选择器" in schema["function"]["parameters"]["properties"]["window"]["description"]


def test_display_params_for_window_ops():
    assert BashOptFunc.display_params(
        {"task_id": "bg-1", "op": "windows"}
    ) == "'windows bg-1 窗口清单'"
    assert BashOptFunc.display_params(
        {"task_id": "bg-1", "op": "window", "window_action": "maximize",
         "window": "#2"}
    ) == "'window bg-1 maximize window=#2'"
    assert BashOptFunc.display_params(
        {"task_id": "bg-1", "op": "screenshot", "path": "a.png",
         "window": "popup", "grid": 50}
    ) == "'screenshot bg-1 a.png window=popup grid=50'"
    assert BashOptFunc.display_params(
        {"task_id": "bg-1", "op": "click", "window": "#1", "shot": True}
    ) == "'click bg-1 left @center window=#1 shot'"


# ── op=windows ───────────────────────────────────────────

async def test_windows_op_lists_windows(monkeypatch):
    infos = [
        WindowInfo(handle=0x10, pid=99, title="主窗口", class_name="Chrome_Window",
                   width=800, height=600, order=0, main=True, foreground=True),
        WindowInfo(handle=0x11, pid=99, title="", class_name="Chrome_Window",
                   width=200, height=150, order=1),
    ]
    monkeypatch.setattr(bash_opt_module, "list_process_windows", lambda pid: infos)
    func = BashOptFunc(task_id="bg-1", op="windows")
    func.set_agent(_FakeAgent({"bg-1": _record(pid=99)}))

    payload = json.loads(await func.execute())
    assert payload["op"] == "windows"
    assert payload["total"] == 2
    assert payload["windows"][0]["handle_hex"] == "0x10"
    assert payload["windows"][1]["title"] == ""
    assert "window" in payload["hint"]
    assert "主窗口" in payload["summary"]


async def test_windows_op_without_process_handle():
    func = BashOptFunc(task_id="bg-1", op="windows")
    func.set_agent(_FakeAgent({"bg-1": _record(pid=None)}))
    result = await func.execute()
    assert result.startswith("(")
    assert "进程句柄" in result


async def test_windows_op_without_windows(monkeypatch):
    monkeypatch.setattr(bash_opt_module, "list_process_windows", lambda pid: [])
    func = BashOptFunc(task_id="bg-1", op="windows")
    func.set_agent(_FakeAgent({"bg-1": _record(pid=7)}))
    payload = json.loads(await func.execute())
    assert payload["total"] == 0
    assert "未找到可见窗口" in payload["hint"]


# ── op=window（窗口控制） ─────────────────────────────────

async def test_window_op_passes_control_request(monkeypatch):
    seen = {}

    def _control(pid, request):
        seen["pid"] = pid
        seen["request"] = request
        return {"window_action": request.action, "handle": 0x10,
                "before": {"x": 0}, "after": {"x": 100}}

    monkeypatch.setattr(bash_opt_module, "control_process_window", _control)
    func = BashOptFunc(task_id="bg-1", op="window", window_action="move",
                       window="#2", x=100, y=200)
    func.set_agent(_FakeAgent({"bg-1": _record(pid=55)}))

    payload = json.loads(await func.execute())
    assert seen["pid"] == 55
    assert isinstance(seen["request"], WindowControlRequest)
    assert seen["request"].action == "move"
    assert (seen["request"].x, seen["request"].y) == (100, 200)
    assert seen["request"].selector == "#2"
    assert payload["op"] == "window"
    assert payload["window_action"] == "move"
    assert payload["handle"] == 0x10


async def test_window_op_rejects_missing_action():
    func = BashOptFunc(task_id="bg-1", op="window")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    result = await func.execute()
    assert result.startswith("(")
    assert "窗口控制参数非法" in result


async def test_window_op_rejects_missing_geometry():
    func = BashOptFunc(task_id="bg-1", op="window", window_action="resize",
                       width=200)
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    result = await func.execute()
    assert "窗口控制参数非法" in result
    assert "height" in result


async def test_window_op_reports_selector_error(monkeypatch):
    def _control(pid, request):
        raise SelectorError("窗口选择器 '#9' 没有匹配的窗口")

    monkeypatch.setattr(bash_opt_module, "control_process_window", _control)
    func = BashOptFunc(task_id="bg-1", op="window", window_action="maximize")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    result = await func.execute()
    assert result.startswith("(窗口控制失败")
    assert "没有匹配的窗口" in result


async def test_window_op_reports_backend_error(monkeypatch):
    def _control(pid, request):
        raise ScreenshotError("当前平台后端（macos）暂不支持窗口控制")

    monkeypatch.setattr(bash_opt_module, "control_process_window", _control)
    func = BashOptFunc(task_id="bg-1", op="window", window_action="close")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    result = await func.execute()
    assert "暂不支持窗口控制" in result


async def test_window_op_timeout(monkeypatch):
    import time as time_module
    monkeypatch.setattr(BashOptFunc, "_INPUT_TIMEOUT", 0.05)

    def _slow(pid, request):
        time_module.sleep(0.3)
        return {}

    monkeypatch.setattr(bash_opt_module, "control_process_window", _slow)
    func = BashOptFunc(task_id="bg-1", op="window", window_action="minimize")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    result = await func.execute()
    assert "窗口控制超时" in result


# ── 输入 op 的 window / shot / settle ────────────────────

async def test_input_op_passes_window_selector(monkeypatch):
    recorder = {}
    _fake_send(monkeypatch, recorder)
    func = BashOptFunc(task_id="bg-1", op="click", x=1, y=2, window="#3")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    await func.execute()
    assert recorder["action"].window == "#3"


async def test_input_op_rejects_bad_window_selector(monkeypatch):
    recorder = {}
    _fake_send(monkeypatch, recorder)
    func = BashOptFunc(task_id="bg-1", op="click", window="#0")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    result = await func.execute()
    assert "输入参数非法" in result
    assert not recorder


async def test_input_op_shot_captures_after_injection(monkeypatch, tmp_path):
    _fast_retry(monkeypatch)
    send_recorder = {}
    capture_recorder = {}
    _fake_send(monkeypatch, send_recorder)
    _fake_capture(monkeypatch, capture_recorder)
    func = BashOptFunc(task_id="bg-1", op="click", x=1, y=2,
                       shot=str(tmp_path / "after.png"))
    func.set_agent(_FakeAgent({"bg-1": _record(pid=77)}))

    payload = json.loads(await func.execute())
    assert payload["screenshot"]["path"].endswith("after.png")
    assert payload["screenshot"]["width"] == 32
    assert capture_recorder["pid"] == 77
    assert "read_image" in payload["hint"]


async def test_input_op_shot_true_uses_auto_directory(monkeypatch, tmp_path):
    _fast_retry(monkeypatch)
    monkeypatch.chdir(tmp_path)
    capture_recorder = {}
    _fake_send(monkeypatch, {})
    _fake_capture(monkeypatch, capture_recorder)
    func = BashOptFunc(task_id="bg-1", op="key", key="enter", shot=True)
    func.set_agent(_FakeAgent({"bg-1": _record(pid=1)}))

    payload = json.loads(await func.execute())
    path = payload["screenshot"]["path"]
    assert BashOptFunc._SHOT_AUTO_DIR in path
    assert path.endswith(".png")


async def test_input_op_shot_failure_recorded_without_losing_result(monkeypatch, tmp_path):
    _fast_retry(monkeypatch)
    _fake_send(monkeypatch, {})
    _fake_capture(monkeypatch, {}, error=ScreenshotError("没有可见窗口"))
    func = BashOptFunc(task_id="bg-1", op="click", shot=str(tmp_path / "x.png"))
    func.set_agent(_FakeAgent({"bg-1": _record(pid=5)}))

    payload = json.loads(await func.execute())
    assert payload["action"] == "click"      # 注入结果仍返回
    assert "没有可见窗口" in payload["screenshot_error"]
    assert "screenshot" not in payload


async def test_input_op_shot_passes_window_and_grid(monkeypatch, tmp_path):
    _fast_retry(monkeypatch)
    _fake_send(monkeypatch, {})
    recorder = {}
    _fake_capture(monkeypatch, recorder)
    func = BashOptFunc(task_id="bg-1", op="click", window="popup",
                       grid=40, shot=str(tmp_path / "w.png"))
    func.set_agent(_FakeAgent({"bg-1": _record(pid=9)}))
    await func.execute()
    assert recorder["window"] == "popup"
    assert recorder["grid"] == 40


def test_resolve_settle_rules():
    assert BashOptFunc(task_id="bg-1", op="click")._resolve_settle() == 0.0
    assert BashOptFunc(task_id="bg-1", op="click", shot="a.png")._resolve_settle() == pytest.approx(
        BashOptFunc._DEFAULT_SHOT_SETTLE)
    assert BashOptFunc(task_id="bg-1", op="click", settle="0.5")._resolve_settle() == pytest.approx(0.5)
    assert BashOptFunc(task_id="bg-1", op="click", settle=999)._resolve_settle() == pytest.approx(
        BashOptFunc._MAX_SETTLE_SECONDS)


async def test_input_op_rejects_bad_settle(monkeypatch):
    _fake_send(monkeypatch, {})
    func = BashOptFunc(task_id="bg-1", op="click", settle="soon")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    result = await func.execute()
    assert "输入参数非法" in result


# ── screenshot 的 window / grid ──────────────────────────

async def test_screenshot_passes_window_and_grid(monkeypatch, tmp_path):
    _fast_retry(monkeypatch)
    recorder = {}
    _fake_capture(monkeypatch, recorder)
    func = BashOptFunc(task_id="bg-1", op="screenshot",
                       path=str(tmp_path / "win.png"), window="#2", grid=100)
    func.set_agent(_FakeAgent({"bg-1": _record(pid=11)}))

    payload = json.loads(await func.execute())
    assert recorder["window"] == "#2"
    assert recorder["grid"] == 100
    assert payload["window"] == "#2"
    assert payload["grid"] == 100
    assert "参考线" in payload["hint"]


async def test_screenshot_grid_true_means_auto(monkeypatch, tmp_path):
    _fast_retry(monkeypatch)
    recorder = {}
    _fake_capture(monkeypatch, recorder)
    func = BashOptFunc(task_id="bg-1", op="screenshot",
                       path=str(tmp_path / "auto.png"), grid=True)
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    await func.execute()
    assert recorder["grid"] == 0


async def test_screenshot_rejects_bad_grid(monkeypatch, tmp_path):
    recorder = {}
    _fake_capture(monkeypatch, recorder)
    func = BashOptFunc(task_id="bg-1", op="screenshot",
                       path=str(tmp_path / "bad.png"), grid="wide")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    result = await func.execute()
    assert "截图网格参数非法" in result
    assert not recorder


async def test_screenshot_reports_selector_error(monkeypatch, tmp_path):
    _fast_retry(monkeypatch)

    def _boom(pid, path, crop=None, **kwargs):
        raise SelectorError("窗口选择器 'title:x' 没有匹配的窗口")

    monkeypatch.setattr(bash_opt_module, "capture_process_window", _boom)
    func = BashOptFunc(task_id="bg-1", op="screenshot",
                       path=str(tmp_path / "sel.png"), window="title:x")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    result = await func.execute()
    assert result.startswith("(截图失败")
    assert "没有匹配的窗口" in result


def test_resolve_grid_variants():
    func = BashOptFunc(task_id="bg-1", op="screenshot", grid="50")
    assert func._resolve_grid() == 50
    assert BashOptFunc(task_id="bg-1", op="screenshot")._resolve_grid() is None
    assert BashOptFunc(task_id="bg-1", op="screenshot", grid=False)._resolve_grid() is None
    assert BashOptFunc(task_id="bg-1", op="screenshot", grid=True)._resolve_grid() == 0
    assert BashOptFunc(task_id="bg-1", op="screenshot", grid=-5)._resolve_grid() == 0
