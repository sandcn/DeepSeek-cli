"""SubAgent 后台 bash 任务防卡死回归测试。

覆盖 Bug 修复（2026-08-08）：
- 多个 SubAgent 并发执行时，任一 SubAgent 的长时 bash 命令（前台执行超过
  _AUTO_BG_TIMEOUT 自动转后台）会让 SubAgent 在 _process_background_tasks
  中**无限等待**该任务完成 → 整个并行执行永久卡死。
- 修复：等待带 _BACKGROUND_WAIT_TIMEOUT 超时，超时后标记任务
  managed_by_tool（由 bash_task 工具管理）并插入「仍在运行」用户消息，
  SubAgent 正常结束。
- 补充：SubAgent 结束时清理内部未完成的后台 bash 任务（task + 子进程），
  防止 asyncio task 与子进程长期残留（fd/进程资源累积卡死）。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from src.core import base_agent
from src.core.agent import Agent
from src.core.base_agent import BaseAgent
from src.core.ports.model import AsyncModelPort, ModelResult
from src.core.subagent import SubAgent
from src.tools.bash import BashFunc


class _LongBashPort(AsyncModelPort):
    """模型端口：第1轮调用长时 bash（自动转后台），后续返回最终结果。"""

    def __init__(self, final_content: str = "最终结果"):
        self.i = 0
        self.final_content = final_content

    async def call(self, messages, model=None, tools=None, display=None,
                   label=None, silent=False, **kwargs):
        self.i += 1
        if self.i == 1:
            return ModelResult(
                reasoning="", content="",
                usage={"input": 1, "output": 1},
                tool_calls=[{
                    "id": "call_b", "name": "bash",
                    "arguments": {"command": "sleep 100"},
                }],
            )
        return ModelResult(
            reasoning="", content=self.final_content,
            usage={"input": 1, "output": 1}, tool_calls=[],
        )

    async def call_sync(self, messages, model=None, tools=None, display=None,
                        label=None, **kwargs):
        return await self.call(messages, model, tools, display, label)


async def _cleanup_bg_tasks(sub: SubAgent) -> None:
    """清理 SubAgent 中残留的后台任务（后台 bash 任务注册在 SubAgent 上）。

    注意：SubAgent.run() 的 finally 已清理 _background_tasks，此函数仅作为
    断言失败路径的兜底，防止子进程/任务泄漏。
    """
    for task_id in list(getattr(sub, "_background_tasks", {}) or {}):
        rec = sub._background_tasks.pop(task_id, None)
        if rec is None:
            continue
        pid = rec.get("pid")
        if pid is not None:
            try:
                from src.tools.bash import kill_process_tree
                kill_process_tree(pid)
            except Exception:
                pass
        task = rec.get("task")
        if task is not None and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=2)
            except BaseException:
                # CancelledError 继承 BaseException（Python 3.8+），必须用
                # BaseException 捕获，否则断言失败路径会掩盖真实失败原因
                pass


def _make_subagent(agent: Agent) -> SubAgent:
    """构造绑定测试模型端口的 SubAgent。"""
    sub = SubAgent(
        label="agent-1",
        description="测试",
        prompt="执行任务",
        parent_agent=agent,
        model="fake-model",
        agent_type="execute",
    )
    sub._model_port = _LongBashPort()
    return sub


class TestSubAgentBackgroundWaitTimeout:
    """SubAgent 后台 bash 任务等待超时防卡死。"""

    @pytest.mark.asyncio
    async def test_bg_wait_timeout_does_not_hang(self, monkeypatch) -> None:
        """长时 bash 转后台后，SubAgent 在超时后正常结束（不无限卡死）。

        回归：修复前 SubAgent 在 _process_background_tasks 中无限等待
        sleep 100 任务，asyncio.wait_for(15s) 必超时。
        """
        monkeypatch.setattr(BashFunc, "_AUTO_BG_TIMEOUT", 1)
        monkeypatch.setattr(base_agent, "_BACKGROUND_WAIT_TIMEOUT", 2)

        agent = Agent(model="fake-model")
        sub = _make_subagent(agent)
        port = sub._model_port

        try:
            result = await asyncio.wait_for(sub.run(), timeout=15)
        finally:
            await _cleanup_bg_tasks(sub)

        assert result == "最终结果"
        # 超时后插入「仍在运行」消息 → 模型再走一轮 → 正常结束（共 3 次调用）
        assert port.i == 3

    @pytest.mark.asyncio
    async def test_running_msg_inserted_after_timeout(self, monkeypatch) -> None:
        """等待超时后插入「仍在运行」用户消息（status=running）。"""
        monkeypatch.setattr(BashFunc, "_AUTO_BG_TIMEOUT", 1)
        monkeypatch.setattr(base_agent, "_BACKGROUND_WAIT_TIMEOUT", 2)

        agent = Agent(model="fake-model")
        sub = _make_subagent(agent)

        try:
            await asyncio.wait_for(sub.run(), timeout=15)
        finally:
            await _cleanup_bg_tasks(sub)

        running_msgs = [
            json.loads(m["content"]) for m in sub.messages
            if m["role"] == "user" and m["content"].startswith("{")
        ]
        assert len(running_msgs) == 1
        assert running_msgs[0]["status"] == "running"
        assert running_msgs[0]["task_id"].startswith("bg-")

    @pytest.mark.asyncio
    async def test_background_tasks_cleaned_on_exit(self, monkeypatch) -> None:
        """SubAgent 结束后清理内部后台任务记录（防资源泄漏）。"""
        monkeypatch.setattr(BashFunc, "_AUTO_BG_TIMEOUT", 1)
        monkeypatch.setattr(base_agent, "_BACKGROUND_WAIT_TIMEOUT", 2)

        agent = Agent(model="fake-model")
        sub = _make_subagent(agent)

        try:
            await asyncio.wait_for(sub.run(), timeout=15)
        finally:
            await _cleanup_bg_tasks(sub)

        # 后台任务被清理（不再残留未完成的 task/进程句柄）
        assert sub._background_tasks == {}


class TestWaitBackgroundTasksTimeout:
    """BaseAgent._wait_background_tasks 带超时返回未完成任务。"""

    @pytest.mark.asyncio
    async def test_wait_returns_unfinished_on_timeout(self) -> None:
        """超时后返回仍未完成的任务集合（不无限阻塞）。"""
        agent = BaseAgent()

        async def never_done():
            await asyncio.sleep(100)

        task = asyncio.ensure_future(never_done())
        try:
            unfinished = await agent._wait_background_tasks([task], timeout=0.3)
            assert task in unfinished
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_wait_returns_empty_when_done(self) -> None:
        """任务在超时前完成 → 返回空集合。"""
        agent = BaseAgent()

        async def quick():
            return None

        task = asyncio.ensure_future(quick())
        try:
            unfinished = await agent._wait_background_tasks([task], timeout=5)
            assert task not in unfinished
            assert not unfinished
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


class TestConcurrentScheduleNoDup:
    """多个并发 schedule()（模拟并行 SubAgent）工具不重复执行（P0-2 回归）。

    修复前：多个 SubAgent 的 _execute_global_dag_async 并发遍历同一全局 DAG，
    非最外层不标记 _pending_tc_ids → 可能同时选中同一层节点 → 工具重复执行
    （write_file 写两次 / bash 跑两次）。修复后：选中节点一律标记 pending。
    """

    @pytest.mark.asyncio
    async def test_concurrent_schedule_no_dup(self) -> None:
        from unittest.mock import AsyncMock, MagicMock, patch

        from src.core.tool_executor_async import ToolScheduler

        scheduler = ToolScheduler()
        scheduler._reset_global_state()
        scheduler._schedule_lock = asyncio.Lock()

        dispatch_log: list[str] = []
        mock_func = AsyncMock()
        mock_func.execute = AsyncMock(return_value="ok")

        def fake_dispatch(name, arguments, agent=None):
            dispatch_log.append(arguments.get("path", name))
            return mock_func

        async def _run_func(func, tc):
            return await func.execute()

        with patch.object(scheduler._registry, "dispatch",
                          side_effect=fake_dispatch):
            agent_a = MagicMock(label="agent-1", agent_type="execute")
            agent_b = MagicMock(label="agent-2", agent_type="execute")

            tc_a = [
                {"id": "call_a1", "name": "write_file",
                 "arguments": {"path": "/tmp/a1.txt", "content": "x"}},
                {"id": "call_a2", "name": "write_file",
                 "arguments": {"path": "/tmp/a2.txt", "content": "x"}},
            ]
            tc_b = [
                {"id": "call_b1", "name": "write_file",
                 "arguments": {"path": "/tmp/b1.txt", "content": "x"}},
                {"id": "call_b2", "name": "write_file",
                 "arguments": {"path": "/tmp/b2.txt", "content": "x"}},
            ]

            async def sched_a():
                return await scheduler.schedule(
                    tc_a, agent_ref=agent_a, run_method=_run_func)

            async def sched_b():
                return await scheduler.schedule(
                    tc_b, agent_ref=agent_b, run_method=_run_func)

            results = await asyncio.wait_for(
                asyncio.gather(sched_a(), sched_b()), timeout=15)

        # 4 个工具应恰好执行一次（无重复执行）
        assert len(dispatch_log) == 4
        assert sorted(dispatch_log) == [
            "/tmp/a1.txt", "/tmp/a2.txt", "/tmp/b1.txt", "/tmp/b2.txt",
        ]
        # 每个批次返回自己的 2 个结果
        assert len(results[0]) == 2
        assert len(results[1]) == 2

