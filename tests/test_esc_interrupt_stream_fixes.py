"""ESC 中断 × 流式输出：修复回归测试。

覆盖四组修复（2026-10）：

1. **流式等待可被中断**（``api.stream.pipeline_async._interruptible_iter_async``）：
   等待 SSE 下一块数据期间，中断信号与「取块」并行竞速——用户按 ESC 后
   立即结束迭代，不再被 120s 空闲超时阻塞；空闲超时语义不变。
2. **ESC 杀后台任务保全已完成结果**（``core.base_agent._kill_all_background_tasks``）：
   清表前先收集「已完成但尚未被对话消费」的后台结果并插入对话，避免随
   ``clear()`` 永久丢失；运行中任务按 ESC 语义取消。
3. **中断保留已生成内容**（``core.pipeline._execute_model_call_async``）：
   中断时把流式已累积的 content 落库（而非固定占位），仅在无内容时用占位符。
4. **ESC 保留未提交输入**（``tui._input_dispatcher._do_interrupt``）：中断
   不再清空用户正在编辑但尚未提交的缓冲内容。
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

from src.api.stream import pipeline_async as pa
from src.api.stream.pipeline_async import StreamContext
from src.core.adapters.interrupt import MockInterruptAdapter
from src.core.base_agent import BaseAgent
from src.core.pipeline import Pipeline, PipelineContext
from src.tui._input_buffer import InputBufferEditor
from src.tui._input_dispatcher import InputDispatcher
from src.tui._input_io import InputIO
from src.tui._input_parser import InputParser


# ═══════════════════════════════════════════════════════════
# 1. 流式等待可被中断（P0）
# ═══════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_interrupt_breaks_stalled_stream_quickly(monkeypatch):
    """等待 SSE 下一块期间中断 → 立即结束迭代（不再等 120s 空闲超时）。"""
    monkeypatch.setattr(pa, "_INTERRUPT_POLL_INTERVAL", 0.02)

    async def stalled():
        await asyncio.Event().wait()  # 永不产出（模拟 SSE 卡住）
        yield {}  # pragma: no cover

    state = {"interrupted": False}

    async def _is_interrupted() -> bool:
        return state["interrupted"]

    monkeypatch.setattr(pa, "is_interrupted_async", _is_interrupted)

    ctx = StreamContext("m", None, None, True)

    async def _run() -> None:
        async for _chunk in pa._interruptible_iter_async(stalled(), ctx):
            pass

    async def _trigger() -> None:
        await asyncio.sleep(0.2)
        state["interrupted"] = True

    start = time.monotonic()
    await asyncio.gather(_run(), _trigger())
    elapsed = time.monotonic() - start

    # 远小于 _STREAM_IDLE_TIMEOUT（120s）；留出调度余量
    assert elapsed < 2.0


@pytest.mark.asyncio
async def test_already_interrupted_returns_without_reading(monkeypatch):
    """中断标志已置位时：不启动下一块读取，直接结束迭代。"""
    reads = []

    async def gen():
        reads.append("anext")
        yield {"choices": []}

    async def _is_interrupted() -> bool:
        return True

    monkeypatch.setattr(pa, "is_interrupted_async", _is_interrupted)

    ctx = StreamContext("m", None, None, True)
    out = []
    async for chunk in pa._interruptible_iter_async(gen(), ctx):
        out.append(chunk)

    assert out == []
    assert reads == []  # 未触发上游读取


@pytest.mark.asyncio
async def test_idle_timeout_still_raises(monkeypatch):
    """空闲超时语义不变：中断未发生且无数据 → StreamIdleTimeoutError。"""
    monkeypatch.setattr(pa, "_STREAM_IDLE_TIMEOUT", 0.1)

    async def stalled():
        await asyncio.Event().wait()
        yield {}  # pragma: no cover

    async def _not_interrupted() -> bool:
        return False

    monkeypatch.setattr(pa, "is_interrupted_async", _not_interrupted)

    ctx = StreamContext("m", None, None, True)

    async def _run() -> None:
        async for _chunk in pa._interruptible_iter_async(stalled(), ctx):
            pass

    with pytest.raises(pa.StreamIdleTimeoutError):
        await asyncio.wait_for(_run(), timeout=10)


# ═══════════════════════════════════════════════════════════
# 2. ESC 杀后台任务保全已完成结果（P1-1）
# ═══════════════════════════════════════════════════════════

class _StubMainAgent(BaseAgent):
    """最小主 Agent：显式持有 subagent 任务表（仅主 Agent 独有）。"""

    def __init__(self):
        super().__init__()
        self._subagent_tasks: dict[str, dict] = {}


def _bash_record(task, *, done=False, stdout="", stderr="", returncode=None,
                 status="running"):
    return {
        "task": task,
        "cwd": None,
        "created_at": 0.0,
        "done": done,
        "result": "",
        "status": status,
        "process": None,
        "pid": None,
        "mode": "pipe",
        "master_fd": None,
        "stdin_writer": None,
        "io_lock": None,
        "read_buffer": "",
        "stdout": stdout,
        "stderr": stderr,
        "returncode": returncode,
    }


@pytest.mark.asyncio
async def test_kill_preserves_completed_background_results():
    """杀后台任务前，已完成任务的结果进入对话消息（不随清表丢失）。"""
    agent = _StubMainAgent()

    done_task = asyncio.ensure_future(asyncio.sleep(0))
    await done_task
    agent._background_tasks["bg-done"] = _bash_record(
        done_task, done=True, stdout="RESULT-OK\n", returncode=0,
        status="completed",
    )
    run_task = asyncio.ensure_future(asyncio.sleep(100))
    agent._background_tasks["bg-run"] = _bash_record(run_task)

    count = await agent._kill_all_background_tasks()

    assert count == 1  # 仅取消运行中的任务
    assert agent._background_tasks == {}
    assert run_task.cancelled()
    contents = "\n".join(str(m.get("content", "")) for m in agent.messages)
    assert "RESULT-OK" in contents
    assert "bg-done" in contents
    try:
        await run_task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_kill_without_completed_results_inserts_nothing():
    """无已完成结果时不插入任何对话消息（零副作用）。"""
    agent = _StubMainAgent()
    run_task = asyncio.ensure_future(asyncio.sleep(100))
    agent._background_tasks["bg-run"] = _bash_record(run_task)

    before = len(agent.messages)
    await agent._kill_all_background_tasks()
    assert len(agent.messages) == before
    assert run_task.cancelled()
    try:
        await run_task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_kill_does_not_insert_cancelled_task_results():
    """被本次 ESC 取消的任务不产生「已取消」结果消息（中断语义干净）。

    完成回调（bash ``_on_done``）会在任务被取消后写 done/result——保全发生在
    取消**之前**，故这些「被中断取消」的任务不会被误当已完成结果插入对话。
    """
    import json

    agent = _StubMainAgent()

    async def _long():
        await asyncio.sleep(100)

    task = asyncio.ensure_future(_long())
    agent._background_tasks["bg-run"] = _bash_record(task)

    def _on_done(t):
        # 模拟 bash._promote_to_background 的完成回调：取消 → 写"已取消"结果
        agent._complete_background_task(
            "bg-run",
            json.dumps({"stdout": "", "stderr": "(后台命令已被取消)",
                        "returncode": None}, ensure_ascii=False),
        )

    task.add_done_callback(_on_done)

    await agent._kill_all_background_tasks()

    contents = "\n".join(str(m.get("content", "")) for m in agent.messages)
    assert "bg-run" not in contents
    assert "已被取消" not in contents
    assert agent._background_tasks == {}
    try:
        await task
    except asyncio.CancelledError:
        pass


# ═══════════════════════════════════════════════════════════
# 3. 中断保留已生成内容（P1-2）
# ═══════════════════════════════════════════════════════════

class _StubModelAgent:
    """Model 调用桩 Agent（供 Pipeline._execute_model_call_async 测试）。"""

    def __init__(self, response):
        self.messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ]
        self.model = "mock-model"
        self.display = None
        self.tools = [{"function": {"name": "t"}}]
        self.agent_id = "stub-1"
        self.agent_kind = "main"
        self._capture_mgr = None
        self._response = response

    def _get_active_tools(self):
        return self.tools

    def _append_assistant_msg(self, content, reasoning=None):
        self.messages.append(
            {"role": "assistant", "content": content, "reasoning_content": reasoning},
        )

    async def _call_model_async(self, messages, model=None, tools=None, display=None,
                                label=None, silent=False):
        return self._response


@pytest.mark.asyncio
async def test_interrupted_call_keeps_partial_content():
    """中断时保留流式已生成的部分内容（而非固定占位）。"""
    agent = _StubModelAgent(("", "部分回答 (已中断)", {}, []))
    ctx = PipelineContext(agent)
    port = MockInterruptAdapter()
    port.set_interrupted(True)
    ctx.interrupt_port = port

    await Pipeline()._execute_model_call_async(ctx)

    assert ctx.interrupted is True
    assert agent.messages[-1]["content"] == "部分回答 (已中断)"


@pytest.mark.asyncio
async def test_interrupted_call_without_content_uses_placeholder():
    """中断且无任何内容时落中断占位符。"""
    agent = _StubModelAgent(("", "", {}, []))
    ctx = PipelineContext(agent)
    port = MockInterruptAdapter()
    port.set_interrupted(True)
    ctx.interrupt_port = port

    await Pipeline()._execute_model_call_async(ctx)

    assert ctx.interrupted is True
    assert agent.messages[-1]["content"] == "(已中断)"


# ═══════════════════════════════════════════════════════════
# 4. ESC 保留未提交输入（P1-4）
# ═══════════════════════════════════════════════════════════

def _make_real_dispatcher():
    """构造真实 InputBufferEditor + 真实 InputIO（pipe fd）的 dispatcher。"""
    r, w = os.pipe()
    io = InputIO(r)
    be = InputBufferEditor(Path("/dev/null"))
    return InputDispatcher(io, be, InputParser(io=io)), be, r, w


def test_do_interrupt_keeps_unsubmitted_input():
    """ESC 中断保留用户正在编辑但尚未提交的输入（不清空缓冲）。"""
    d, be, r, w = _make_real_dispatcher()
    try:
        be.set_buffer("未提交的草稿")
        d._do_interrupt()
        assert be.get_current_text() == "未提交的草稿"
    finally:
        os.close(r)
        os.close(w)


def test_reset_default_still_clears_buffer():
    """``reset()`` 默认仍清空缓冲（既有语义不变）。"""
    be = InputBufferEditor(Path("/dev/null"))
    be.set_buffer("abc")
    be.reset()
    assert be.get_current_text() == ""


def test_reset_keep_buffer_clears_queue_and_navigation():
    """``reset(clear_buffer=False)`` 保留缓冲，同时清历史导航/搜索/撤销态。"""
    be = InputBufferEditor(Path("/dev/null"))
    be.set_buffer("keep-me")
    be._history_idx = 2
    be._search_active = True
    be._undo_stack.append(("x", 0))

    be.reset(clear_buffer=False)

    assert be.get_current_text() == "keep-me"
    assert be._history_idx == -1
    assert be._search_active is False
    assert be._undo_stack == []


def test_reset_and_echo_keep_buffer():
    """``reset_and_echo(clear_buffer=False)`` 回显保留的缓冲内容。"""
    r, w = os.pipe()
    try:
        io = InputIO(r)
        be = InputBufferEditor(Path("/dev/null"))
        echoed: list[tuple[str, int]] = []
        be.set_echo_callback(lambda text, pos=-1: echoed.append((text, pos)))
        d = InputDispatcher(io, be, InputParser(io=io))

        be.set_buffer("保留")
        d.reset_and_echo(clear_buffer=False)
        assert be.get_current_text() == "保留"
        assert echoed and echoed[-1][0] == "保留"
    finally:
        os.close(r)
        os.close(w)


def test_do_interrupt_with_queued_input_keeps_queue():
    """存在未消费排队输入时，ESC 不清输入、只清 stdin 残留（既有保护不变）。"""
    d, be, r, w = _make_real_dispatcher()
    try:
        be.set_buffer("draft")
        be._submitted_text = "queued"
        be._input_ready.set()
        d._do_interrupt()
        # 排队输入未被消费（仍是提交态）
        assert be.has_queued_input() is True
        assert be.get_current_text() == "draft"
    finally:
        os.close(r)
        os.close(w)
