"""bash_task 工具 read 操作测试。

read 操作语义：读取后台任务**当前已产生**的全部输出并清空缓冲，立即返回
（不等待任务完成）。后续 read 只返回新产生的输出（增量）；任务最终完整
结果由 op=wait 获取。

覆盖：
- schema 的 op 枚举包含 read
- _op_read 单元测试：读取全部输出并清空缓冲 / 空缓冲 / 已完成任务状态
- 集成测试：后台任务实时增量读取 + 立即返回 + 最终 wait
- 集成测试：前台命令自动转后台后 read 仍可实时读取
- 未知 op 错误信息
"""
from __future__ import annotations

import asyncio
import json
import time

from src.tools.bash import BashFunc
from src.tools.bash_task import BashTaskFunc


class FakeAgent:
    """最小 Agent 替身：实现 bash 工具依赖的后台任务记录契约。

    与 src/core/base_agent.py 的 BaseAgent 接口保持一致
    （_register_background_task / _complete_background_task /
    _remove_background_task / _background_tasks）。
    """

    def __init__(self):
        self._background_tasks: dict = {}

    def _register_background_task(self, task_id: str, record: dict) -> None:
        self._background_tasks[task_id] = record

    def _complete_background_task(self, task_id: str, result: str,
                                  status: str = "completed") -> None:
        rec = self._background_tasks.get(task_id)
        if rec is not None:
            rec["result"] = result
            rec["status"] = status
            rec["done"] = True

    def _remove_background_task(self, task_id: str) -> dict | None:
        return self._background_tasks.pop(task_id, None)


def _make_rec(command: str = "echo hi", read_buffer: str = "") -> dict:
    """构造一条最小后台任务记录（含 read 操作所需字段）。"""
    return {
        "task": None,
        "command": command,
        "done": False,
        "result": "",
        "status": "running",
        "io_lock": asyncio.Lock(),
        "read_buffer": read_buffer,
    }


async def _wait_until(predicate, timeout: float = 10.0) -> None:
    """轮询等待 predicate() 为真，超时抛 AssertionError。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("等待条件超时未满足")


# ── schema ───────────────────────────────────────────────

def test_schema_includes_read_op():
    """schema 的 op 枚举必须包含 read。"""
    params = BashTaskFunc.to_tool_schema()["function"]["parameters"]
    enum = params["properties"]["op"]["enum"]
    assert "read" in enum


# ── 单元测试：_op_read ──────────────────────────────────

async def test_op_read_returns_all_output_and_clears_buffer():
    """read 返回 read_buffer 全部内容并把缓冲清空。"""
    agent = FakeAgent()
    rec = _make_rec(command="echo hi", read_buffer="line1\nline2\n")
    agent._background_tasks["bg-abc"] = rec

    tool = BashTaskFunc(task_id="bg-abc", op="read")
    tool.set_agent(agent)
    out = await tool.execute()

    payload = json.loads(out)
    assert payload["task_id"] == "bg-abc"
    assert payload["command"] == "echo hi"
    assert payload["status"] == "running"
    assert payload["output"] == "line1\nline2\n"
    # 缓冲已清空
    assert rec["read_buffer"] == ""


async def test_op_read_empty_buffer_returns_empty_output():
    """缓冲为空时 read 返回空 output，不报错。"""
    agent = FakeAgent()
    rec = _make_rec(command="echo hi", read_buffer="")
    agent._background_tasks["bg-abc"] = rec

    tool = BashTaskFunc(task_id="bg-abc", op="read")
    tool.set_agent(agent)
    payload = json.loads(await tool.execute())
    assert payload["output"] == ""
    assert rec["read_buffer"] == ""


async def test_op_read_done_task_reports_completed_status():
    """任务已完成时 read 的 status 反映 completed。"""
    agent = FakeAgent()
    rec = _make_rec(command="echo hi", read_buffer="final\n")
    rec["done"] = True
    rec["status"] = "completed"
    agent._background_tasks["bg-abc"] = rec

    tool = BashTaskFunc(task_id="bg-abc", op="read")
    tool.set_agent(agent)
    payload = json.loads(await tool.execute())
    assert payload["status"] == "completed"
    assert payload["output"] == "final\n"
    assert rec["read_buffer"] == ""


async def test_read_missing_task_returns_error():
    """task_id 不存在时 read 报错（与其他 op 一致）。"""
    agent = FakeAgent()
    tool = BashTaskFunc(task_id="bg-not-exist", op="read")
    tool.set_agent(agent)
    out = await tool.execute()
    assert out.startswith("(")
    assert "后台任务不存在" in out


async def test_unknown_op_message_lists_read():
    """未知 op 的错误信息列出 read/wait/kill/stdin/keys。"""
    agent = FakeAgent()
    agent._background_tasks["bg-x"] = _make_rec()
    tool = BashTaskFunc(task_id="bg-x", op="bogus")
    tool.set_agent(agent)
    out = await tool.execute()
    assert "未知操作" in out
    assert "read/wait/kill/stdin/keys" in out


# ── 集成测试：真实后台任务（background=True） ────────────

async def test_read_incremental_and_immediate():
    """后台任务运行中：read 立即返回已产生输出并清空，增量不重复，wait 拿最终结果。"""
    agent = FakeAgent()
    cmd = BashFunc("echo start; sleep 3; echo end", background=True)
    cmd.set_agent(agent)
    r = await cmd.execute()
    tid = json.loads(r)["task_id"]

    # 等 "start" 行进入 read_buffer（PTY 行缓冲下立即出现）
    await _wait_until(lambda: "start" in agent._background_tasks[tid].get("read_buffer", ""))

    # read：立即返回（任务仍在 sleep，未完成），返回已产生输出并清空
    tool = BashTaskFunc(task_id=tid, op="read")
    tool.set_agent(agent)
    t0 = time.monotonic()
    out = await tool.execute()
    elapsed = time.monotonic() - t0
    p1 = json.loads(out)
    assert p1["status"] == "running"      # 不等待任务完成
    assert elapsed < 2.0                    # 立即返回
    assert "start" in p1["output"]
    assert agent._background_tasks[tid]["read_buffer"] == ""

    # 增量验证：再次 read 不重复出现 start
    tool2 = BashTaskFunc(task_id=tid, op="read")
    tool2.set_agent(agent)
    p2 = json.loads(await tool2.execute())
    assert "start" not in p2["output"]

    # wait：任务完成后拿到最终完整结果
    tool3 = BashTaskFunc(task_id=tid, op="wait", timeout=15)
    tool3.set_agent(agent)
    p3 = json.loads(await tool3.execute())
    assert p3["status"] == "completed"
    assert "start" in p3["output"]
    assert "end" in p3["output"]


async def test_background_record_initializes_read_buffer():
    """background=True 注册的后台任务记录必须初始化 read_buffer 字段。"""
    agent = FakeAgent()
    cmd = BashFunc("echo x; sleep 5", background=True)
    cmd.set_agent(agent)
    r = await cmd.execute()
    tid = json.loads(r)["task_id"]
    rec = agent._background_tasks[tid]
    assert "read_buffer" in rec
    assert rec["read_buffer"] == ""
    # 清理
    kill = BashTaskFunc(task_id=tid, op="kill")
    kill.set_agent(agent)
    await kill.execute()


# ── 集成测试：前台命令自动转后台后 read 仍可用 ──────────

async def test_auto_promoted_background_supports_read(monkeypatch):
    """前台命令超过 _AUTO_BG_TIMEOUT 自动转后台后，read 可实时读取输出。

    ★ 语义边界：自动转后台前的输出（模型尚未拿到 task_id）不进入
    read_buffer，最终由 wait 返回完整结果；转后台后（拿到 task_id 后）
    产生的输出可被 read 实时增量读取。故首个输出安排在转后台之后。
    """
    agent = FakeAgent()
    monkeypatch.setattr(BashFunc, "_AUTO_BG_TIMEOUT", 0.2)
    cmd = BashFunc("sleep 0.5; echo auto-1; sleep 2; echo auto-2", background=False)
    cmd.set_agent(agent)
    r = await cmd.execute()
    tid = json.loads(r)["task_id"]

    # 自动转后台记录含 read_buffer
    rec = agent._background_tasks[tid]
    assert "read_buffer" in rec

    # 等输出进入 read_buffer（转后台后经 _line_proxy 追加）
    await _wait_until(lambda: "auto-1" in rec.get("read_buffer", ""))

    tool = BashTaskFunc(task_id=tid, op="read")
    tool.set_agent(agent)
    p = json.loads(await tool.execute())
    assert p["status"] == "running"
    assert "auto-1" in p["output"]
    assert rec["read_buffer"] == ""

    # 清理
    kill = BashTaskFunc(task_id=tid, op="kill")
    kill.set_agent(agent)
    await kill.execute()
