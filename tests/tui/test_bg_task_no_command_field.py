"""后台 bash 任务移除 command 字段测试（2026-10-06）。

需求：后台 bash（background=True 启动 / 前台超时自动转后台）相关的
**所有对外输出与任务记录**一律不再携带 command 字段：

  1. bash.py：_execute_background / _promote_to_background 返回 JSON、
     注册到 _background_tasks 的任务记录；
  2. bash_opt.py：op=read / op=wait 返回 JSON；
  3. base_agent.py：完成后注入对话的用户消息、超时未完成时插入的
     「仍在运行」用户消息。

subagent 后台任务（_subagent_tasks）的 command 字段（任务描述）不受影响，
仍由 subagent_opt / _collect_done_subagent_messages 使用。
"""

from __future__ import annotations

import asyncio
import contextlib
import json

from src.core.base_agent import BaseAgent
from src.tools.bash import BashFunc
from src.tools.bash_opt import BashOptFunc


class _FakeAgent:
    """最小 Agent 桩：bash 专用表 _background_tasks + 注册/完成/移除方法。"""

    def __init__(self):
        self._background_tasks: dict = {}

    def _register_background_task(self, tid, rec):
        self._background_tasks[tid] = rec

    def _complete_background_task(self, tid, result):
        rec = self._background_tasks.get(tid)
        if rec is None:
            return
        rec["result"] = result
        rec["done"] = True
        rec["status"] = "completed"

    def _remove_background_task(self, tid):
        return self._background_tasks.pop(tid, None)

    def _publish_background_task_event(self):
        pass


class _RealAgent(BaseAgent):
    """真实 BaseAgent 子类：捕获 _append_background_result_messages 插入的消息。"""

    def __init__(self):
        super().__init__()
        self.notified: list[str] = []

    def add_user_message(self, content):
        self.notified.append(content)

    def _publish_background_task_event(self):
        pass


# ── 1. bash.py：后台启动 / 自动转后台 ─────────────────────

async def test_background_start_json_and_record_have_no_command():
    """background=True：返回 JSON 与任务记录均无 command 键。"""
    agent = _FakeAgent()
    f = BashFunc(command="sleep 5", background=True)
    f.set_agent(agent)

    payload = json.loads(await f.execute())
    assert set(payload) == {"task_id", "status"}
    assert payload["status"] == "running"

    rec = agent._background_tasks[payload["task_id"]]
    assert "command" not in rec
    assert rec["task"] is not None

    opt = BashOptFunc(task_id=payload["task_id"], op="kill")
    opt.set_agent(agent)
    await opt.execute()


async def test_auto_promote_json_and_record_have_no_command():
    """前台超时自动转后台：返回 JSON 与任务记录均无 command 键。"""
    agent = _FakeAgent()
    f = BashFunc(command="sleep 300")
    f.set_agent(agent)

    async def _never():
        await asyncio.sleep(300)

    exec_task = asyncio.ensure_future(_never())
    try:
        result = await f._promote_to_background(exec_task, {}, {"fn": None})
        payload = json.loads(result)
        assert set(payload) == {"task_id", "status"}
        assert payload["status"] == "running"

        rec = agent._background_tasks[payload["task_id"]]
        assert "command" not in rec
        assert rec["task"] is exec_task
    finally:
        exec_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await exec_task


# ── 2. bash_opt.py：read / wait ──────────────────────────

async def test_bash_opt_read_and_wait_have_no_command():
    """op=read / op=wait 返回 JSON 均无 command 键。"""
    agent = _FakeAgent()
    tid = "bg-readwait"
    agent._background_tasks[tid] = {
        "read_buffer": "line1\n",
        "status": "running",
        "done": False,
    }

    read_func = BashOptFunc(task_id=tid, op="read")
    read_func.set_agent(agent)
    read_payload = json.loads(await read_func.execute())
    assert set(read_payload) == {"task_id", "status", "output"}
    assert read_payload["output"] == "line1\n"

    agent._background_tasks[tid] = {
        "task": None,
        "done": True,
        "status": "completed",
        "stdout": "o",
        "stderr": "e",
        "returncode": 0,
    }
    wait_func = BashOptFunc(task_id=tid, op="wait")
    wait_func.set_agent(agent)
    wait_payload = json.loads(await wait_func.execute())
    assert set(wait_payload) == {
        "task_id", "status", "stdout", "stderr", "returncode",
    }
    assert tid not in agent._background_tasks


# ── 3. base_agent.py：注入对话的消息 ─────────────────────

def test_collect_done_message_has_no_command():
    """已完成后台 bash 结果消息无 command 键（三元展开保持不变）。"""
    agent = _FakeAgent()
    tid = "bg-done"
    agent._background_tasks[tid] = {
        "task": None,
        "done": True,
        "status": "completed",
        "result": json.dumps(
            {"stdout": "x", "stderr": "", "returncode": 0}),
    }

    msgs = BaseAgent._collect_done_background_messages(agent)
    assert len(msgs) == 1
    payload = json.loads(msgs[0])
    assert set(payload) == {
        "task_id", "status", "stdout", "stderr", "returncode",
    }


async def test_running_message_has_no_command():
    """超时未完成时插入的「仍在运行」消息无 command 键。"""
    agent = _RealAgent()
    tid = "bg-running"

    async def _never():
        await asyncio.sleep(100)

    task = asyncio.ensure_future(_never())
    agent._background_tasks[tid] = {
        "task": task,
        "done": False,
        "status": "running",
        "stdout": "",
        "stderr": "",
        "returncode": None,
    }

    async def _fake_wait(tasks, timeout=None):
        return set(tasks)

    agent._wait_background_tasks = _fake_wait
    try:
        handled = await agent._process_background_tasks()
        assert handled is True
        assert agent.notified, "应插入「仍在运行」提示消息"
        payload = json.loads(agent.notified[0])
        assert set(payload) == {
            "task_id", "status", "stdout", "stderr", "returncode",
        }
        assert payload["status"] == "running"
        assert agent._background_tasks[tid]["managed_by_tool"] is True
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


# ── 4. subagent 侧 command 字段保持（不受本次改动影响） ──

def test_subagent_message_keeps_command_field():
    """subagent 后台任务结果消息仍保留 command（任务描述）。"""
    agent = _FakeAgent()
    agent._subagent_tasks = {
        "sa-1": {
            "task": None,
            "command": "subagent(解析 user.py)",
            "done": True,
            "status": "completed",
            "result": "ok",
        },
    }

    msgs = BaseAgent._collect_done_subagent_messages(agent)
    assert len(msgs) == 1
    payload = json.loads(msgs[0])
    assert payload["command"] == "subagent(解析 user.py)"
    assert payload["output"] == "ok"
