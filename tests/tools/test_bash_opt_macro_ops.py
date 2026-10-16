"""bash_opt 操作宏（op=record / op=replay）测试。

覆盖：schema 暴露、保存宏（macro / path 两种方式）、追加模式、回放（单次 /
多次）、参数校验与错误提示。回放用纯 wait 步骤，不触发真实输入注入。
"""

from __future__ import annotations

import json
import os

import pytest

from src.tools.bash_opt import BashOptFunc
from src.tools._window_input.macro import load_macro


class _FakeAgent:
    def __init__(self, records: dict):
        self._background_tasks = records
        self._subagent_tasks = {}


def _record(pid=4321):
    return {"read_buffer": "", "status": "running", "done": False, "pid": pid,
            "task": None}


def _run(prefix=None, **kwargs):
    func = BashOptFunc(task_id="bg-1", **kwargs)
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    return func


@pytest.fixture(autouse=True)
def _macro_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(BashOptFunc, "_MACRO_DIR", str(tmp_path / "macros"))
    return tmp_path / "macros"


# ── schema ──────────────────────────────────────────────

def test_schema_exposes_macro_parameters():
    params = BashOptFunc.to_tool_schema()["function"]["parameters"]
    enum = params["properties"]["op"]["enum"]
    assert "record" in enum and "replay" in enum
    for name in ("macro", "times", "append"):
        assert name in params["properties"], name


# ── record ──────────────────────────────────────────────

async def test_record_saves_macro_by_name():
    result = await _run(op="record", macro="demo",
                        actions=[{"op": "wait", "seconds": 0}]).execute()
    payload = json.loads(result)
    assert payload["macro"] == "demo"
    assert payload["steps"] == 1
    assert os.path.isfile(payload["path"])
    loaded = load_macro(name="demo", directory=BashOptFunc._MACRO_DIR)
    assert loaded.step_count == 1


async def test_record_saves_macro_by_path(tmp_path):
    target = tmp_path / "custom.json"
    result = await _run(op="record", path=str(target),
                        actions=[{"op": "wait", "seconds": 0}]).execute()
    payload = json.loads(result)
    assert payload["path"].endswith("custom.json")
    assert os.path.isfile(payload["path"])


async def test_record_append_mode():
    await _run(op="record", macro="m",
               actions=[{"op": "wait", "seconds": 0}]).execute()
    await _run(op="record", macro="m", append=True,
               actions=[{"op": "wait", "seconds": 0}]).execute()
    loaded = load_macro(name="m", directory=BashOptFunc._MACRO_DIR)
    assert loaded.step_count == 2


async def test_record_requires_actions():
    result = await _run(op="record", macro="m").execute()
    assert "actions" in result


async def test_record_requires_name_or_path():
    result = await _run(op="record",
                        actions=[{"op": "wait", "seconds": 0}]).execute()
    assert "macro" in result and "path" in result


async def test_record_rejects_invalid_steps():
    result = await _run(op="record", macro="m",
                        actions=[{"foo": "bar"}]).execute()
    assert result.startswith("(record 失败")


# ── replay ──────────────────────────────────────────────

async def test_replay_runs_saved_macro():
    await _run(op="record", macro="demo",
               actions=[{"op": "wait", "seconds": 0},
                        {"op": "wait", "seconds": 0}]).execute()
    result = await _run(op="replay", macro="demo").execute()
    payload = json.loads(result)
    assert payload["op"] == "replay"
    assert payload["times"] == 1
    assert payload["executed"] == 1
    assert payload["completed"] == 2
    assert payload["steps_per_run"] == 2


async def test_replay_repeats_times():
    await _run(op="record", macro="demo",
               actions=[{"op": "wait", "seconds": 0}]).execute()
    payload = json.loads(await _run(op="replay", macro="demo", times=3).execute())
    assert payload["executed"] == 3
    assert payload["completed"] == 3
    assert [run["iteration"] for run in payload["runs"]] == [1, 2, 3]


async def test_replay_by_path(tmp_path):
    target = tmp_path / "p.json"
    await _run(op="record", path=str(target),
               actions=[{"op": "wait", "seconds": 0}]).execute()
    payload = json.loads(await _run(op="replay", path=str(target)).execute())
    assert payload["completed"] == 1


async def test_replay_missing_macro():
    result = await _run(op="replay", macro="nope").execute()
    assert result.startswith("(replay 失败")


async def test_replay_requires_name_or_path():
    result = await _run(op="replay").execute()
    assert "macro" in result and "path" in result


async def test_replay_rejects_bad_times():
    await _run(op="record", macro="demo",
               actions=[{"op": "wait", "seconds": 0}]).execute()
    result = await _run(op="replay", macro="demo", times=0).execute()
    assert "times" in result
