"""bash_opt 控件能力测试（op=elements 与输入 op 的 element 参数）。

覆盖：schema 暴露、控件清单输出（含窗口内坐标换算 / element 过滤 / 空清单 /
选择器错误 / 条数限制）、输入 op 按控件名定位（click 用控件中心、type 先点击
聚焦、与显式坐标互斥、定位失败提示）。
"""

from __future__ import annotations

import json

from src.tools import bash_opt as bash_opt_module
from src.tools._screenshot.elements import ElementInfo
from src.tools._screenshot.windows import SelectorError
from src.tools._window_input.geometry import WindowFrame
from src.tools._window_input.result import InputResult
from src.tools.bash_opt import BashOptFunc


class _FakeAgent:
    def __init__(self, records: dict):
        self._background_tasks = records
        self._subagent_tasks = {}


def _record(pid=4321):
    return {"read_buffer": "", "status": "running", "done": False, "pid": pid,
            "task": None}


def _elements():
    return [
        ElementInfo(handle=0x20, pid=99, class_name="Button", text="确定",
                    left=200, top=150, width=80, height=30),
        ElementInfo(handle=0x21, pid=99, class_name="Edit", text="",
                    left=100, top=100, width=400, height=40),
    ]


async def _fake_frame(self, pid, window):
    return WindowFrame(100, 50, 800, 600)


def _patch_elements(monkeypatch, elements=None, error=None):
    def _impl(pid, window=None):
        if error is not None:
            raise error
        return list(elements if elements is not None else _elements())
    monkeypatch.setattr(bash_opt_module, "list_process_elements", _impl)


def _patch_send(monkeypatch, recorder: list):
    def _impl(pid, action):
        recorder.append(action)
        return InputResult(action=action.name, backend="windows", window_pid=pid,
                           window_title="App", detail={"delivery": "sendinput"})
    monkeypatch.setattr(bash_opt_module, "send_window_input", _impl)


# ── schema ──────────────────────────────────────────────

def test_schema_exposes_control_lookup_parameters():
    params = BashOptFunc.to_tool_schema()["function"]["parameters"]
    enum = params["properties"]["op"]["enum"]
    assert "elements" in enum and "wait_window" in enum
    assert "clipboard" in enum and "sequence" in enum
    for name in ("element", "max_elements", "actions", "on_error",
                 "clipboard_action", "via", "paste_key", "restore_clipboard",
                 "wait_for", "wait_timeout", "diff", "tolerance"):
        assert name in params["properties"], name
    assert params["properties"]["via"]["enum"] == ["typing", "clipboard"]


# ── op=elements ─────────────────────────────────────────

async def test_elements_requires_process_handle():
    func = BashOptFunc(task_id="bg-1", op="elements")
    func.set_agent(_FakeAgent({"bg-1": {"read_buffer": "", "status": "running",
                                        "done": False}}))
    assert "尚无进程句柄" in await func.execute()


async def test_elements_lists_controls_with_window_coordinates(monkeypatch):
    _patch_elements(monkeypatch)
    monkeypatch.setattr(BashOptFunc, "_window_frame_for", _fake_frame)
    func = BashOptFunc(task_id="bg-1", op="elements")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    payload = json.loads(await func.execute())
    assert payload["total"] == 2 and payload["matched"] == 2
    first = payload["elements"][0]
    assert first["text"] == "确定" and first["type"] == "button"
    # 屏幕坐标 (240,165) → 窗口内 (140,115)（frame 原点 100,50）
    assert (first["window_center_x"], first["window_center_y"]) == (140, 115)
    assert first["window_x"] == 100 and first["window_y"] == 100
    assert "click" in payload["hint"]


async def test_elements_filters_by_element_substring(monkeypatch):
    _patch_elements(monkeypatch)
    monkeypatch.setattr(BashOptFunc, "_window_frame_for", _fake_frame)
    func = BashOptFunc(task_id="bg-1", op="elements", element="Edit")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    payload = json.loads(await func.execute())
    assert payload["matched"] == 1
    assert payload["elements"][0]["class"] == "Edit"


async def test_elements_reports_empty_and_selector_errors(monkeypatch):
    _patch_elements(monkeypatch, elements=[])
    func = BashOptFunc(task_id="bg-1", op="elements")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    payload = json.loads(await func.execute())
    assert payload["total"] == 0 and "自绘界面" in payload["hint"]

    _patch_elements(monkeypatch, error=SelectorError("窗口选择器 '#9' 没有匹配的窗口"))
    result = await func.execute()
    assert result.startswith("(枚举控件失败") and "op=windows" in result


async def test_elements_rejects_bad_limit(monkeypatch):
    _patch_elements(monkeypatch)
    func = BashOptFunc(task_id="bg-1", op="elements", max_elements=0)
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    assert "max_elements" in await func.execute()


# ── 输入 op 的 element 参数 ─────────────────────────────

async def test_click_with_element_uses_control_center(monkeypatch):
    _patch_elements(monkeypatch)
    monkeypatch.setattr(BashOptFunc, "_window_frame_for", _fake_frame)
    recorder: list = []
    _patch_send(monkeypatch, recorder)
    func = BashOptFunc(task_id="bg-1", op="click", element="确定")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    payload = json.loads(await func.execute())
    assert len(recorder) == 1
    assert (recorder[0].x, recorder[0].y) == (140, 115)
    assert payload["element"]["label"] == "确定"


async def test_type_with_element_clicks_then_types(monkeypatch):
    _patch_elements(monkeypatch)
    monkeypatch.setattr(BashOptFunc, "_window_frame_for", _fake_frame)
    recorder: list = []
    _patch_send(monkeypatch, recorder)
    func = BashOptFunc(task_id="bg-1", op="type", text="hello", element="Edit")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    await func.execute()
    assert [action.name for action in recorder] == ["click", "type"]
    assert (recorder[0].x, recorder[0].y) == (200, 70)  # Edit 中心换算到窗口内


async def test_element_conflicts_with_explicit_coordinates(monkeypatch):
    _patch_elements(monkeypatch)
    func = BashOptFunc(task_id="bg-1", op="click", element="确定", x=1, y=2)
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    assert "不能同时提供" in await func.execute()


async def test_element_reports_missing_controls(monkeypatch):
    _patch_elements(monkeypatch, elements=[])
    func = BashOptFunc(task_id="bg-1", op="click", element="确定")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    result = await func.execute()
    assert result.startswith("(控件定位失败") and "自绘界面" in result


async def test_element_reports_no_match(monkeypatch):
    _patch_elements(monkeypatch)
    monkeypatch.setattr(BashOptFunc, "_window_frame_for", _fake_frame)
    func = BashOptFunc(task_id="bg-1", op="click", element="不存在")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    result = await func.execute()
    assert "没有匹配" in result
