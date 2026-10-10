"""bash_opt 游戏操作增强的工具层测试（release / relative_event / hold_keys / 会话）。

覆盖：schema 暴露新 op 与参数、工具参数到输入动作的打包（release / 相对位移 /
分阶段点击 / 长按 / hold_keys）、display_params 摘要、sequence 的输入会话包裹。
"""

from __future__ import annotations

import json

import pytest

from src.tools import bash_opt as bash_opt_module
from src.tools._window_input.result import InputResult
from src.tools.bash_opt import BashOptFunc


class _FakeAgent:
    def __init__(self, records: dict):
        self._background_tasks = records
        self._subagent_tasks = {}


def _record(pid=4321):
    return {"read_buffer": "", "status": "running", "done": False,
            "pid": pid, "task": None}


def _fake_send(monkeypatch, recorder: dict):
    def _impl(pid, action):
        recorder["pid"] = pid
        recorder["action"] = action
        return InputResult(action=action.name, backend="windows", window_pid=pid,
                           window_title="Game", detail={"delivery": "sendinput"})
    monkeypatch.setattr(bash_opt_module, "send_window_input", _impl)


# ── schema ───────────────────────────────────────────────

def test_schema_exposes_release_and_game_parameters():
    params = BashOptFunc.to_tool_schema()["function"]["parameters"]["properties"]
    assert "release" in params["op"]["enum"]
    for name in ("relative_event", "hold_keys", "keys", "buttons"):
        assert name in params, name
    assert "视角" in params["relative_event"]["description"]
    assert "hold" in params["hold_keys"]["description"]
    assert "100" in params["count"]["description"]
    assert "click" in params["phase"]["description"]


def test_schema_op_description_mentions_game_enhancements():
    params = BashOptFunc.to_tool_schema()["function"]["parameters"]["properties"]
    op_description = params["op"]["description"]
    assert "release" in op_description
    assert "视角" in op_description
    assert "hold_keys" in op_description


# ── display_params ───────────────────────────────────────

def test_display_params_game_ops():
    assert BashOptFunc.display_params(
        {"task_id": "bg-1", "op": "release"}
    ) == "'release bg-1 全部'"
    assert BashOptFunc.display_params(
        {"task_id": "bg-1", "op": "release", "keys": ["w", "a"]}
    ) == "'release bg-1 keys=w/a'"
    assert BashOptFunc.display_params(
        {"task_id": "bg-1", "op": "move", "dx": 5, "dy": 0, "relative_event": True}
    ) == "'move bg-1 rel dx=5 dy=0 event'"
    assert BashOptFunc.display_params(
        {"task_id": "bg-1", "op": "click", "phase": "down"}
    ) == "'click bg-1 left down @center'"
    assert BashOptFunc.display_params(
        {"task_id": "bg-1", "op": "key", "key": "w", "hold": 0.5}
    ) == "'key bg-1 w hold=0.5s'"


# ── 参数打包 ─────────────────────────────────────────────

async def test_release_op_builds_release_action(monkeypatch):
    recorder = {}
    _fake_send(monkeypatch, recorder)
    func = BashOptFunc(task_id="bg-1", op="release", keys=["w"], buttons=["left"])
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    payload = json.loads(await func.execute())
    action = recorder["action"]
    assert action.name == "release"
    assert action.keys == ("w",)
    assert action.buttons == ("left",)
    assert payload["action"] == "release"


async def test_move_relative_event_built(monkeypatch):
    recorder = {}
    _fake_send(monkeypatch, recorder)
    func = BashOptFunc(task_id="bg-1", op="move", dx=12, dy=-3,
                       relative_event=True, steps=3, interval=0.01)
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    await func.execute()
    action = recorder["action"]
    assert action.uses_relative_events is True
    assert action.interval == pytest.approx(0.01)


async def test_click_phase_and_hold_keys_built(monkeypatch):
    recorder = {}
    _fake_send(monkeypatch, recorder)
    func = BashOptFunc(task_id="bg-1", op="click", phase="down", hold_keys=["w"])
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    await func.execute()
    action = recorder["action"]
    assert action.phase == "down"
    assert action.hold_keys == ("w",)


async def test_key_hold_and_interval_built(monkeypatch):
    recorder = {}
    _fake_send(monkeypatch, recorder)
    func = BashOptFunc(task_id="bg-1", op="key", key="w", hold=0.4,
                       interval=0.02, repeat=5)
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    await func.execute()
    action = recorder["action"]
    assert action.hold == pytest.approx(0.4)
    assert action.interval == pytest.approx(0.02)
    assert action.repeat == 5


# ── sequence 输入会话 ────────────────────────────────────

async def test_sequence_wraps_input_session(monkeypatch):
    recorder = {}
    _fake_send(monkeypatch, recorder)
    counters = {"begin": 0, "end": 0}

    def _begin(pid):
        counters["begin"] += 1
        return True

    def _end():
        counters["end"] += 1

    monkeypatch.setattr(bash_opt_module, "begin_input_session", _begin)
    monkeypatch.setattr(bash_opt_module, "end_input_session", _end)
    func = BashOptFunc(task_id="bg-1", op="sequence", actions=[
        {"op": "key", "key": "w"},
        {"op": "click", "x": 1, "y": 2},
    ])
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    payload = json.loads(await func.execute())
    assert payload["completed"] == 2
    assert counters == {"begin": 1, "end": 1}


async def test_sequence_release_step(monkeypatch):
    recorder = {}
    _fake_send(monkeypatch, recorder)
    monkeypatch.setattr(bash_opt_module, "begin_input_session", lambda pid: False)
    monkeypatch.setattr(bash_opt_module, "end_input_session", lambda: None)
    func = BashOptFunc(task_id="bg-1", op="sequence", actions=[
        {"op": "release"},
    ])
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    payload = json.loads(await func.execute())
    assert payload["completed"] == 1
    assert recorder["action"].name == "release"
