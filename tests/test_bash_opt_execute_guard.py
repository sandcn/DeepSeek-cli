"""bash_opt 执行层的异常兜底测试（健壮性）。"""

from __future__ import annotations

import asyncio

import pytest

from src.tools.bash_opt import BashOptFunc


class _FakeAgent:
    def __init__(self, records: dict):
        self._background_tasks = records
        self._subagent_tasks = {}


def _record():
    return {"read_buffer": "hello", "status": "running", "done": False,
            "pid": 4321, "task": None}


def _func(op="read"):
    func = BashOptFunc(task_id="bg-1", op=op)
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    return func


async def test_normal_path_unaffected():
    assert "hello" in await _func("read").execute()


async def test_internal_error_becomes_readable_message(monkeypatch):
    async def boom(self, rec):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(BashOptFunc, "_op_read", boom)
    result = await _func("read").execute()
    assert "内部错误" in result
    assert "kaboom" in result


async def test_cancelled_error_propagates(monkeypatch):
    async def cancel(self, rec):
        raise asyncio.CancelledError()
    monkeypatch.setattr(BashOptFunc, "_op_read", cancel)
    with pytest.raises(asyncio.CancelledError):
        await _func("read").execute()
