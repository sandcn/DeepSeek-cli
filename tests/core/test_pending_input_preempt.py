"""用户输入优先：等待后台 bash/subagent 期间消费消息队列测试（2026-10-19 用户需求）。

需求：agent loop 在等待后台 bash 和 subagent 时，只要消息队列有消息，
就要消费一条——不能一直等到后台任务完成（最长 120s）后才处理用户输入。

实现机制：
  1. BaseAgent 新增 ``_pending_input_probe``（上层注入，返回 True 表示消息
     队列有用户消息）与 ``_background_wait_preempted`` 标志；
  2. ``_wait_background_tasks`` 每 0.2s 轮询探测（含进入等待前），有用户
     消息即提前返回当前未完成任务集合并置标志——后台任务继续运行、
     不杀、不标记 managed_by_tool；
  3. ``_process_background_tasks`` / ``_process_subagent_tasks`` 读取标志后
     立即复位并返回 False（本轮不处理后台任务、不插入「仍在运行」消息），
     让当前轮尽快结束、由消息队列消费者取走并处理该条用户消息；
  4. app_loop / clawbot TUI 在创建消息队列后注入探测（队列 + 输入框排队）。
"""

from __future__ import annotations

import asyncio

import pytest

from src.app_loop._loop import _make_pending_input_probe
from src.core.adapters.interrupt import MockInterruptAdapter
from src.core.base_agent import BaseAgent
from src.core.interrupt_state import (
    request_kill_background,
    reset_interrupt_async,
    reset_kill_background,
)
from src.core.message_queue import AsyncMessageQueue


@pytest.fixture(autouse=True)
def _reset_interrupt_state():
    """隔离全局中断/kill 标志（等待循环每次迭代都会检查）。"""
    reset_interrupt_async()
    reset_kill_background()
    yield
    reset_interrupt_async()
    reset_kill_background()


# ── 测试辅助 ──────────────────────────────────────────────

class _FakeMainAgent(BaseAgent):
    """最小主 Agent 桩（显式初始化 subagent 任务表，与 Agent 一致）。"""

    def __init__(self):
        super().__init__()
        self._subagent_tasks: dict[str, dict] = {}


def _make_bash_rec(task, managed=False, done=False):
    """构造 bash 后台任务记录（结构与 bash.py _promote_to_background 一致）。"""
    return {
        "task": task,
        "cwd": None,
        "created_at": 0.0,
        "done": done,
        "result": "",
        "status": "running",
        "process": None,
        "pid": None,
        "mode": "pipe",
        "master_fd": None,
        "stdin_writer": None,
        "io_lock": None,
        "read_buffer": "",
        **({"managed_by_tool": True} if managed else {}),
    }


def _make_sa_rec(task, managed=False, done=False):
    """构造 subagent 后台任务记录（结构与 subagent.py _execute_background 一致）。"""
    return {
        "task": task,
        "command": "subagent(任务A)",
        "description": "任务A",
        "agent_type": "execute",
        "created_at": 0.0,
        "done": done,
        "result": "",
        "status": "running",
        "read_buffer": "",
        **({"managed_by_tool": True} if managed else {}),
    }


async def _long_running():
    await asyncio.sleep(100)


async def _cancel(task):
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# ── _wait_background_tasks：用户输入抢占 ──────────────────

async def test_wait_background_tasks_preempted_by_pending_input():
    """消息队列有消息时立即结束等待：返回未完成任务、置标志、不杀任务。"""
    agent = _FakeMainAgent()
    agent.set_pending_input_probe(lambda: True)
    task = asyncio.ensure_future(_long_running())
    agent._background_tasks["bg-1"] = _make_bash_rec(task)

    try:
        started = asyncio.get_running_loop().time()
        unfinished = await agent._wait_background_tasks([task], timeout=30)
        elapsed = asyncio.get_running_loop().time() - started

        assert unfinished == {task}          # 任务仍在运行，未完成
        assert agent._background_wait_preempted is True
        assert not task.cancelled() and not task.done()  # 后台任务未杀
        assert elapsed < 1.0                 # 不等满 30s 超时
        assert "bg-1" in agent._background_tasks
    finally:
        await _cancel(task)


async def test_wait_background_tasks_without_pending_input_waits():
    """无待处理用户消息时保持原语义：等待任务完成。"""
    agent = _FakeMainAgent()
    agent.set_pending_input_probe(lambda: False)

    async def _finish():
        await asyncio.sleep(0.02)

    task = asyncio.ensure_future(_finish())
    agent._background_tasks["bg-1"] = _make_bash_rec(task)

    unfinished = await agent._wait_background_tasks([task], timeout=5)
    assert unfinished == set()
    assert agent._background_wait_preempted is False
    assert task.done()
    assert "bg-1" in agent._background_tasks


async def test_wait_background_tasks_probe_failure_does_not_break_wait():
    """探测回调抛异常时按「无待处理消息」处理，不打断正常等待。"""
    agent = _FakeMainAgent()

    def _boom():
        raise RuntimeError("probe failed")

    agent.set_pending_input_probe(_boom)

    async def _finish():
        await asyncio.sleep(0.02)

    task = asyncio.ensure_future(_finish())
    unfinished = await agent._wait_background_tasks([task], timeout=5)
    assert unfinished == set()
    assert agent._background_wait_preempted is False
    assert task.done()


async def test_wait_background_tasks_probe_reset_on_next_call():
    """上轮抢占标志不会泄漏到下一轮等待（入口复位）。"""
    agent = _FakeMainAgent()
    agent.set_pending_input_probe(lambda: True)
    task = asyncio.ensure_future(_long_running())
    try:
        await agent._wait_background_tasks([task], timeout=5)
        assert agent._background_wait_preempted is True

        # 切换探测为「无消息」→ 下一轮等待完成，标志复位
        agent.set_pending_input_probe(lambda: False)

        async def _finish():
            await asyncio.sleep(0.02)

        done_task = asyncio.ensure_future(_finish())
        await agent._wait_background_tasks([done_task], timeout=5)
        assert agent._background_wait_preempted is False
        assert done_task.done()
    finally:
        await _cancel(task)


async def test_set_pending_input_probe_none_restores_default():
    """传入 None 清除探测，恢复默认等待语义。"""
    agent = _FakeMainAgent()
    agent.set_pending_input_probe(lambda: True)
    assert agent._has_pending_user_input() is True

    agent.set_pending_input_probe(None)
    assert agent._has_pending_user_input() is False


async def test_kill_background_priority_over_pending_input():
    """ESC kill 与消息队列有消息同时发生时：中断（杀任务）优先，不漏杀。"""
    agent = _FakeMainAgent()
    agent._interrupt_port = MockInterruptAdapter()
    agent._interrupt_port.set_interrupted(True)
    agent.set_pending_input_probe(lambda: True)
    request_kill_background()
    task = asyncio.ensure_future(_long_running())
    agent._background_tasks["bg-1"] = _make_bash_rec(task)

    try:
        unfinished = await agent._wait_background_tasks([task], timeout=5)

        assert unfinished == set()                 # 中断退出语义
        assert task.cancelled()                    # 任务被杀
        assert agent._background_tasks == {}       # 表已清
        assert agent._background_wait_preempted is False  # 不误标为输入抢占
    finally:
        await _cancel(task)


# ── _process_background_tasks / _process_subagent_tasks：抢占分支 ──

async def test_process_background_tasks_preempt_keeps_task_running():
    """bash 表等待被用户消息抢占：返回 False、不标记 managed、不插入消息。"""
    agent = _FakeMainAgent()
    agent.set_pending_input_probe(lambda: True)
    task = asyncio.ensure_future(_long_running())
    rec = _make_bash_rec(task)
    agent._background_tasks["bg-1"] = rec

    try:
        handled = await agent._process_background_tasks()

        assert handled is False
        assert agent._background_wait_preempted is False  # 已复位
        assert "bg-1" in agent._background_tasks          # 表未清
        assert not rec.get("managed_by_tool")             # 未降级为工具管理
        assert rec.get("done") is not True
        assert not task.cancelled() and not task.done()
        # 未向对话插入「仍在运行」用户消息
        assert agent.messages == []
    finally:
        await _cancel(task)


async def test_process_subagent_tasks_preempt_keeps_task_running():
    """subagent 表等待被用户消息抢占：返回 False、不标记 managed、不插入消息。"""
    agent = _FakeMainAgent()
    agent.set_pending_input_probe(lambda: True)
    task = asyncio.ensure_future(_long_running())
    rec = _make_sa_rec(task)
    agent._subagent_tasks["sa-1"] = rec

    try:
        handled = await agent._process_subagent_tasks()

        assert handled is False
        assert agent._background_wait_preempted is False
        assert "sa-1" in agent._subagent_tasks
        assert not rec.get("managed_by_tool")
        assert not task.cancelled() and not task.done()
        assert agent.messages == []
    finally:
        await _cancel(task)


async def test_process_background_tasks_timeout_still_marks_managed():
    """无用户消息时超时语义不变：标记 managed_by_tool 并插入 running 消息。"""
    agent = _FakeMainAgent()
    agent.set_pending_input_probe(lambda: False)
    task = asyncio.ensure_future(_long_running())
    rec = _make_bash_rec(task)
    agent._background_tasks["bg-1"] = rec

    try:
        # 直接缩小等待超时，避免测试等满 120s
        import src.core.base_agent as base_agent_mod

        old = base_agent_mod._BACKGROUND_WAIT_TIMEOUT
        base_agent_mod._BACKGROUND_WAIT_TIMEOUT = 0.05
        try:
            handled = await agent._process_background_tasks()
        finally:
            base_agent_mod._BACKGROUND_WAIT_TIMEOUT = old

        assert handled is True
        assert rec.get("managed_by_tool") is True
        assert len(agent.messages) == 1
        assert "running" in agent.messages[0]["content"]
    finally:
        await _cancel(task)


# ── 端到端：队列消费者在等待被抢占后能取出并处理消息 ─────

async def test_message_queue_consumer_processes_message_after_preempt():
    """agent 等待后台任务被抢占后，消息队列消费者能立即取走一条消息。"""
    agent = _FakeMainAgent()
    queue = AsyncMessageQueue()
    agent.set_pending_input_probe(_make_pending_input_probe(queue))
    task = asyncio.ensure_future(_long_running())
    agent._background_tasks["bg-1"] = _make_bash_rec(task)

    consumed: list[str] = []

    async def _consume_once():
        msg = await queue.get(timeout=1.0)
        if msg is not None:
            consumed.append(msg.content)

    consumer = asyncio.ensure_future(_consume_once())
    await queue.put("用户新消息")

    try:
        handled = await agent._process_background_tasks()
        assert handled is False          # 本轮结束，把执行权让给消息队列
        await consumer
        assert consumed == ["用户新消息"]
        assert not task.cancelled() and not task.done()
    finally:
        await _cancel(task)


# ── _make_pending_input_probe：多形态兼容 ─────────────────

async def test_probe_detects_async_message_queue_property():
    """AsyncMessageQueue.qsize 是 property：非空即 True，消费后 False。"""
    queue = AsyncMessageQueue()
    probe = _make_pending_input_probe(queue)
    assert probe() is False
    await queue.put("x")
    assert probe() is True
    await queue.get()
    assert probe() is False


async def test_probe_detects_asyncio_queue_method():
    """asyncio.Queue.qsize 是方法：同样可探测。"""
    queue: asyncio.Queue = asyncio.Queue()
    probe = _make_pending_input_probe(queue)
    assert probe() is False
    await queue.put("x")
    assert probe() is True


async def test_probe_detects_input_queued_input():
    """输入框排队输入（AI 生成期间 Enter 提交）同样触发探测。"""
    class _Input:
        def __init__(self, queued):
            self._queued = queued

        def has_queued_input(self):
            return self._queued

    probe_empty = _make_pending_input_probe(asyncio.Queue(), _Input(False))
    assert probe_empty() is False
    probe_queued = _make_pending_input_probe(asyncio.Queue(), _Input(True))
    assert probe_queued() is True


def test_probe_is_exception_safe():
    """探测内部异常不外泄（按无消息处理，不影响等待）。"""
    class _BadQueue:
        @property
        def qsize(self):
            raise RuntimeError("boom")

    class _BadInput:
        def has_queued_input(self):
            raise RuntimeError("boom")

    probe = _make_pending_input_probe(_BadQueue(), _BadInput())
    assert probe() is False


async def test_probe_without_input_instance():
    """无输入框实例（非 TUI 上层）时只检查消息队列。"""
    queue = AsyncMessageQueue()
    probe = _make_pending_input_probe(queue)
    assert probe() is False
