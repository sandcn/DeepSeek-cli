"""dispatch_agent 提前返回路径下 tool result 补发回归测试。

背景（P3 修复，2026-08-08）：
- ToolScheduler 的 dispatch_agent 提前返回（_handle_dispatch_agent_early_return）
  会把剩余 dispatch_agent 转为后台任务执行，外层 schedule() 不等待其完成即返回。
- 此时 dispatch 节点的工具结果不会经 schedule() 返回给调用方（batch_results
  在 bg 完成前按 current_batch_ids ∩ _results_map 计算，dispatch 被排除），
  若不在对话中补发 tool result，下一轮模型调用的消息序列缺 tool 消息
  → API 报错 / 模型重发。
- 修复：_bg_dispatch_agents 在结果写入 _results_map 时同步补发到 agent 消息；
  异常兜底 _on_bg_dispatch_done 同样补发失败结果。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.tool_executor_async import ToolScheduler
from src.core.internal.agent._tool_callbacks import ToolCallbackChain


class FakeAgent:
    def __init__(self):
        self.tool_results: list[tuple[str, str]] = []

    def _append_tool_result(self, tool_call_id: str, content: str) -> None:
        self.tool_results.append((tool_call_id, content))


class TestDispatchEarlyReturnToolResult:
    """dispatch_agent 提前返回路径的 tool result 补发。"""

    @pytest.mark.asyncio
    async def test_bg_dispatch_appends_tool_result(self) -> None:
        """正常完成：_bg_dispatch_agents 补发 dispatch 结果到 agent 消息。"""
        scheduler = ToolScheduler()
        scheduler._reset_global_state()
        scheduler._schedule_lock = asyncio.Lock()

        agent = FakeAgent()
        mock_func = AsyncMock()
        mock_func.execute = AsyncMock(return_value="子代理结果")

        remaining = [{"id": "call_d1", "name": "dispatch_agent",
                      "arguments": {"description": "d", "prompt": "p"}}]

        with patch.object(scheduler._registry, "dispatch", return_value=mock_func):
            await scheduler._bg_dispatch_agents(remaining, agent_ref=agent)

        assert agent.tool_results == [("call_d1", "子代理结果")]

    @pytest.mark.asyncio
    async def test_bg_dispatch_done_appends_failure_result(self) -> None:
        """异常兜底：_on_bg_dispatch_done 补发失败结果到 agent 消息。"""
        scheduler = ToolScheduler()
        scheduler._reset_global_state()
        scheduler._schedule_lock = asyncio.Lock()
        # 防御检查要求 _global_dag 非 None（表示清理未发生）
        scheduler._global_dag = object()

        agent = FakeAgent()

        async def _failing():
            raise RuntimeError("dispatch 失败")

        task = asyncio.ensure_future(_failing())
        await asyncio.sleep(0.01)  # 让任务完成
        remaining = [{"id": "call_d2", "name": "dispatch_agent",
                      "arguments": {"description": "d", "prompt": "p"}}]
        scheduler._on_bg_dispatch_done(task, remaining, agent)

        assert len(agent.tool_results) == 1
        assert agent.tool_results[0][0] == "call_d2"
        assert "dispatch 失败" in agent.tool_results[0][1]


class TestDispatchEarlyReturnIntegration:
    """schedule() 端到端早返回集成测试（正常/早返回边界 + 幂等追加）。"""

    @pytest.mark.asyncio
    async def test_early_return_bg_result_appended_once(self) -> None:
        """构造依赖批次触发提前返回：schedule() 不含 dispatch 结果，bg 完成
        后 dispatch tool result 恰好补发一次。"""
        from unittest.mock import MagicMock

        scheduler = ToolScheduler()
        scheduler._reset_global_state()
        scheduler._schedule_lock = asyncio.Lock()

        class FakeAgent:
            def __init__(self):
                self.tool_results: list[tuple[str, str]] = []

            def _append_tool_result(self, tool_call_id: str, content: str) -> None:
                self.tool_results.append((tool_call_id, content))

        agent = FakeAgent()
        dispatch_calls = []

        async def dispatch_execute():
            dispatch_calls.append(1)
            return "子代理结果"

        write_func = AsyncMock()
        write_func.execute = AsyncMock(return_value="写入成功")
        dispatch_func = AsyncMock()
        dispatch_func.execute = AsyncMock(side_effect=dispatch_execute)

        def fake_dispatch(name, args, agent=None):
            if name == "dispatch_agent":
                return dispatch_func
            return write_func

        # call_d 显式依赖 call_w（$call_w 引用）→ 分层：第一层 call_w，第二层 call_d
        tool_calls = [
            {"id": "call_w", "name": "write_file",
             "arguments": {"path": "/tmp/early_x.txt", "content": "x"}},
            {"id": "call_d", "name": "dispatch_agent",
             "arguments": {"description": "d", "prompt": "参考 $call_w 的结果"}},
        ]

        with patch.object(scheduler._registry, "dispatch", side_effect=fake_dispatch):
            results = await scheduler.schedule(tool_calls, agent_ref=agent)
            # 提前返回：schedule() 返回值仅含非 dispatch 节点
            assert [r[0] for r in results] == ["call_w"]
            # 等待后台 dispatch 完成（含补发）
            await scheduler.wait_background_dispatch()

        # dispatch 恰好执行一次、结果恰好补发一次
        assert dispatch_calls == [1]
        assert agent.tool_results == [("call_d", "子代理结果")]


class TestDispatchSummaryNoDup:
    """handle_tool_calls 工具汇总去重（P1-1 回归）。

    dispatch_agent 正常执行（非提前返回）路径下，dispatch 结果已包含在
    schedule() 返回值中；补入汇总的循环必须按 tc_id 去重，避免同一
    dispatch 在 tool_summary 中重复出现。
    """

    @pytest.mark.asyncio
    async def test_normal_dispatch_summary_no_dup(self) -> None:
        scheduler = ToolScheduler()
        scheduler._reset_global_state()
        scheduler._schedule_lock = asyncio.Lock()
        ToolScheduler._default_scheduler = scheduler

        agent = MagicMock()
        agent._display_port = MagicMock()
        agent._display_port.is_web = False
        agent._event_port = MagicMock()
        agent.display = MagicMock()
        agent._on_tool_completed_callbacks = []
        agent.model = "fake"
        agent._shared_executor = None

        summaries: list[tuple[list[str], list[tuple[str, str]]]] = []
        chain = ToolCallbackChain(agent)
        chain._show_tool_execution_summary = (
            lambda s, f: summaries.append((list(s), list(f)))
        )

        async def dispatch_execute():
            return "子代理结果"

        write_func = AsyncMock()
        write_func.execute = AsyncMock(return_value="写入成功")

        async def write_display():
            return "写入成功"

        write_func.display = AsyncMock(side_effect=write_display)
        dispatch_func = AsyncMock()
        dispatch_func.execute = AsyncMock(side_effect=dispatch_execute)

        def fake_dispatch(name, args, agent=None):
            return dispatch_func if name == "dispatch_agent" else write_func

        # 同层无依赖 → dispatch 与 write_file 一起正常执行（不触发提前返回）
        tool_calls = [
            {"id": "call_d", "name": "dispatch_agent",
             "arguments": {"description": "d", "prompt": "p"}},
            {"id": "call_w", "name": "write_file",
             "arguments": {"path": "/tmp/summary_x.txt", "content": "x"}},
        ]

        try:
            with patch.object(scheduler._registry, "dispatch", side_effect=fake_dispatch):
                await chain.handle_tool_calls("", tool_calls)

            assert len(summaries) == 1
            successful = summaries[0][0]
            # dispatch 与 write_file 各恰好出现一次（无重复）
            assert successful.count("dispatch_agent") == 1, f"dispatch 重复: {summaries}"
            assert successful.count("write_file") == 1, f"write 重复: {summaries}"
        finally:
            ToolScheduler._default_scheduler = None


