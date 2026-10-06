"""bash_opt 窗口输入 op（move/click/drag/scroll/key/type）工具层测试。

覆盖：schema 暴露（op 枚举 + x/y/button/count/modifiers/direction/amount/
duration/steps/from_x/from_y/method）、显示摘要、参数打包（含 type 的
newline 语义）、成功路径 JSON、参数非法与无进程句柄的错误透出、暂无窗口
时的轮询重试、超时与注入失败的错误透出、managed_by_tool 标记。
"""

from __future__ import annotations

import json

import pytest

from src.tools import bash_opt as bash_opt_module
from src.tools._window_input import result as input_result
from src.tools._window_input.result import InputResult, NoWindowError
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


def _fast_input_retry(monkeypatch, wait_seconds: float = 0.2, interval: float = 0.01):
    """压缩输入重试参数，避免测试真的等待 5 秒。"""
    monkeypatch.setattr(BashOptFunc, "_INPUT_WAIT_SECONDS", wait_seconds)
    monkeypatch.setattr(BashOptFunc, "_INPUT_RETRY_INTERVAL", interval)


def _fake_send(monkeypatch, recorder: dict, result=None, error=None):
    """替换模块级 send_window_input，记录 (pid, action)。"""
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


# ── schema ───────────────────────────────────────────────

def test_schema_exposes_input_ops_and_parameters():
    schema = BashOptFunc.to_tool_schema()
    params = schema["function"]["parameters"]
    enum = params["properties"]["op"]["enum"]
    for op in ("click", "move", "drag", "scroll", "key", "type"):
        assert op in enum
    for name in ("x", "y", "to_x", "to_y", "from_x", "from_y", "button",
                 "count", "modifiers", "direction", "amount", "duration",
                 "steps", "method", "phase"):
        assert name in params["properties"], name
    assert params["required"] == ["task_id", "op"]
    assert params["properties"]["button"]["enum"] == ["left", "right", "middle"]
    assert params["properties"]["direction"]["enum"] == ["up", "down", "left", "right"]
    assert params["properties"]["method"]["enum"] == ["auto", "sendinput", "message"]
    assert params["properties"]["phase"]["enum"] == ["press", "down", "up"]


def test_schema_descriptions_mention_input_capabilities():
    """op / 工具描述须点明鼠标、双击、拖动、滚轮、按键、文本能力。"""
    schema = BashOptFunc.to_tool_schema()
    description = schema["function"]["description"]
    for keyword in ("click", "drag", "scroll", "type", "screenshot"):
        assert keyword in description
    op_description = schema["function"]["parameters"]["properties"]["op"]["description"]
    assert "双击" in op_description
    assert "鼠标" in op_description
    assert "文本" in op_description


def test_newline_schema_has_no_hard_default():
    """newline 语义按 op 区分（stdin 默认追加、type 默认原样），schema 不写死默认。"""
    schema = BashOptFunc.to_tool_schema()
    newline = schema["function"]["parameters"]["properties"]["newline"]
    assert "default" not in newline
    assert "stdin" in newline["description"] and "type" in newline["description"]


# ── display_params ───────────────────────────────────────

def test_display_params_input_ops():
    assert BashOptFunc.display_params(
        {"task_id": "bg-1", "op": "click", "button": "right", "count": 2,
         "x": 1, "y": 2}
    ) == "'click bg-1 rightx2 @1,2'"
    assert BashOptFunc.display_params(
        {"task_id": "bg-1", "op": "move", "x": 3, "y": 4}
    ) == "'move bg-1 @3,4'"
    assert BashOptFunc.display_params(
        {"task_id": "bg-1", "op": "drag", "to_x": 9, "to_y": 8}
    ) == "'drag bg-1 left @center->@9,8'"
    assert BashOptFunc.display_params(
        {"task_id": "bg-1", "op": "scroll", "direction": "up", "amount": 5}
    ) == "'scroll bg-1 up*5 @center'"
    assert BashOptFunc.display_params(
        {"task_id": "bg-1", "op": "key", "key": "ctrl+s"}
    ) == "'key bg-1 ctrl+s'"
    assert BashOptFunc.display_params(
        {"task_id": "bg-1", "op": "type", "text": "hi"}
    ) == "'type bg-1 hi'"


def test_display_params_single_click_has_no_count():
    assert BashOptFunc.display_params(
        {"task_id": "bg-1", "op": "click", "count": 1}
    ) == "'click bg-1 left @center'"


# ── 成功路径 ─────────────────────────────────────────────

async def test_click_success_returns_json(monkeypatch):
    recorder = {}
    _fake_send(monkeypatch, recorder)
    rec = _record()
    func = BashOptFunc(task_id="bg-1", op="click", button="right", count=2, x=10, y=20)
    func.set_agent(_FakeAgent({"bg-1": rec}))

    payload = json.loads(await func.execute())
    assert payload["task_id"] == "bg-1"
    assert payload["op"] == "click"
    assert payload["action"] == "click"
    assert payload["window_title"] == "Game"
    assert "screenshot" in payload["hint"]
    assert rec["managed_by_tool"] is True
    assert recorder["pid"] == 4321
    assert recorder["action"].button == "right"
    assert recorder["action"].count == 2


async def test_action_uses_only_relevant_parameters(monkeypatch):
    """工具参数按 op 打包：无关字段不污染动作（如 click 不带 to_x）。"""
    recorder = {}
    _fake_send(monkeypatch, recorder)
    func = BashOptFunc(task_id="bg-1", op="click", x=1, y=2,
                       to_x=99, to_y=99, direction="up")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    await func.execute()
    action = recorder["action"]
    assert (action.x, action.y) == (1, 2)
    assert not hasattr(action, "to_x")


async def test_drag_action_built(monkeypatch):
    recorder = {}
    _fake_send(monkeypatch, recorder)
    func = BashOptFunc(task_id="bg-1", op="drag", from_x=1, from_y=2,
                       to_x=3, to_y=4, button="left", duration=0.5, steps=8)
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    await func.execute()
    action = recorder["action"]
    assert (action.from_x, action.from_y) == (1, 2)
    assert (action.to_x, action.to_y) == (3, 4)
    assert action.duration == pytest.approx(0.5)
    assert action.steps == 8


async def test_scroll_action_built(monkeypatch):
    recorder = {}
    _fake_send(monkeypatch, recorder)
    func = BashOptFunc(task_id="bg-1", op="scroll", direction="up", amount=4)
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    await func.execute()
    assert recorder["action"].direction == "up"
    assert recorder["action"].amount == 4


async def test_key_action_built(monkeypatch):
    recorder = {}
    _fake_send(monkeypatch, recorder)
    func = BashOptFunc(task_id="bg-1", op="key", key="ctrl+shift+s")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    await func.execute()
    assert recorder["action"].shortcut.display() == "ctrl+shift+s"


async def test_key_action_phase_built(monkeypatch):
    """key 的 phase 参数透传到动作（down = 只按下，用于长按）。"""
    recorder = {}
    _fake_send(monkeypatch, recorder)
    func = BashOptFunc(task_id="bg-1", op="key", key="ctrl+a", phase="down")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    await func.execute()
    assert recorder["action"].phase == "down"


def test_display_params_marks_key_phase():
    assert BashOptFunc.display_params(
        {"task_id": "bg-1", "op": "key", "key": "ctrl", "phase": "down"}
    ) == "'key bg-1 ctrl down'"


async def test_type_default_does_not_append_newline(monkeypatch):
    recorder = {}
    _fake_send(monkeypatch, recorder)
    func = BashOptFunc(task_id="bg-1", op="type", text="hello")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    await func.execute()
    assert recorder["action"].text == "hello"


async def test_type_newline_true_appends_newline(monkeypatch):
    recorder = {}
    _fake_send(monkeypatch, recorder)
    func = BashOptFunc(task_id="bg-1", op="type", text="hello", newline=True)
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    await func.execute()
    assert recorder["action"].text == "hello\n"


async def test_stdin_default_still_appends_newline(monkeypatch):
    """stdin 的 newline 语义保持（未指定 = 追加换行）。"""
    sent = {}
    rec = _record()
    rec["mode"] = "pipe"

    class _Writer:
        def write(self, data):
            sent["data"] = data

        async def drain(self):
            return None

    rec["stdin_writer"] = _Writer()
    func = BashOptFunc(task_id="bg-1", op="stdin", text="ls")
    func.set_agent(_FakeAgent({"bg-1": rec}))
    await func.execute()
    assert sent["data"] == b"ls\n"


async def test_stdin_newline_false_sends_raw(monkeypatch):
    sent = {}
    rec = _record()
    rec["mode"] = "pipe"

    class _Writer:
        def write(self, data):
            sent["data"] = data

        async def drain(self):
            return None

    rec["stdin_writer"] = _Writer()
    func = BashOptFunc(task_id="bg-1", op="stdin", text="ls", newline=False)
    func.set_agent(_FakeAgent({"bg-1": rec}))
    await func.execute()
    assert sent["data"] == b"ls"


# ── 错误路径 ─────────────────────────────────────────────

async def test_input_without_process_handle(monkeypatch):
    func = BashOptFunc(task_id="bg-1", op="click")
    func.set_agent(_FakeAgent({"bg-1": _record(pid=None)}))
    result = await func.execute()
    assert result.startswith("(")
    assert "进程句柄" in result


async def test_input_invalid_parameters(monkeypatch):
    recorder = {}
    _fake_send(monkeypatch, recorder)
    func = BashOptFunc(task_id="bg-1", op="click", button="wheel")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    result = await func.execute()
    assert result.startswith("(")
    assert "输入参数非法" in result
    assert not recorder


async def test_input_key_missing_parameter(monkeypatch):
    func = BashOptFunc(task_id="bg-1", op="key")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    result = await func.execute()
    assert "输入参数非法" in result


async def test_input_no_window_error_surfaced(monkeypatch):
    _fast_input_retry(monkeypatch)
    recorder = {}
    _fake_send(monkeypatch, recorder, error=NoWindowError("没有可接收输入的窗口"))
    func = BashOptFunc(task_id="bg-1", op="click")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    result = await func.execute()
    assert result.startswith("(输入失败")
    assert "没有可接收输入的窗口" in result


async def test_input_retries_until_window_appears(monkeypatch):
    _fast_input_retry(monkeypatch)
    calls = {"count": 0}

    def _impl(pid, action):
        calls["count"] += 1
        if calls["count"] < 3:
            raise NoWindowError("暂无窗口")
        return InputResult(action=action.name, backend="windows", window_pid=pid,
                           window_title="late", detail={})

    monkeypatch.setattr(bash_opt_module, "send_window_input", _impl)
    func = BashOptFunc(task_id="bg-1", op="click")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    payload = json.loads(await func.execute())
    assert payload["window_title"] == "late"
    assert calls["count"] == 3


async def test_input_platform_error_surfaced(monkeypatch):
    _fast_input_retry(monkeypatch)
    recorder = {}
    _fake_send(monkeypatch, recorder,
               error=input_result.InputError("窗口输入需要 xdotool（未安装）"))
    func = BashOptFunc(task_id="bg-1", op="click")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    result = await func.execute()
    assert "xdotool" in result


async def test_input_timeout_surfaced(monkeypatch):
    _fast_input_retry(monkeypatch)
    monkeypatch.setattr(BashOptFunc, "_INPUT_TIMEOUT", 0.05)

    def _slow(pid, action):
        import time
        time.sleep(0.3)
        return InputResult(action=action.name, backend="windows", window_pid=pid,
                           window_title="slow", detail={})

    monkeypatch.setattr(bash_opt_module, "send_window_input", _slow)
    func = BashOptFunc(task_id="bg-1", op="click")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    result = await func.execute()
    assert result.startswith("(输入失败")
    assert "超时" in result


async def test_unknown_op_lists_input_ops():
    func = BashOptFunc(task_id="bg-1", op="teleport")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    result = await func.execute()
    assert "teleport" in result
    assert "click" in result and "drag" in result


async def test_input_requires_agent_context():
    func = BashOptFunc(task_id="bg-1", op="click")
    result = await func.execute()
    assert "未关联" in result


async def test_input_rejects_non_bg_task_id():
    func = BashOptFunc(task_id="sa-1", op="click")
    func.set_agent(_FakeAgent({}))
    result = await func.execute()
    assert "subagent_opt" in result
