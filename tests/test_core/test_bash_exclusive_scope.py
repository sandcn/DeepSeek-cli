"""测试 bash 独占过滤的作用域修复：仅最外层调度生效，SubAgent 工具不被父 Agent 的 bash 阻塞。

背景（Bug 修复 2026-08-06）：
- ``ToolScheduler._running_bash_ids`` 为全局单例状态。修复前 ``_find_next_layer``
  的 bash 独占过滤对**所有调度上下文**生效——主 Agent 的 bash 运行期间
  （如长时编译 make），SubAgent 嵌套调用的工具（read/write/bash）被过滤成
  空层后无限轮询等待 bash 完成；若 bash 卡住（进程树清理不彻底等），
  子代理永远卡在工具 parsing 状态（用户侧现象：子代理「接收参数后不执行」）。
- 修复：bash 独占过滤**仅对最外层 schedule() 调用**生效（``is_outermost=True``）；
  SubAgent 嵌套调用（``is_outermost=False``）跳过独占过滤，子代理工具可独立
  调度，不被父 Agent 的 bash 阻塞。

测试覆盖：
1. ``_find_next_layer`` 单元行为：最外层被拦截（返回 []），嵌套调用放行。
2. 集成：主 Agent bash 运行中，SubAgent 调度 read 工具不被阻塞（秒级完成）。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.tool_dag import ToolDAG
from src.core.tool_executor_async import ToolScheduler


class TestBashExclusiveScope:
    """bash 独占过滤作用域：仅最外层调度生效。"""

    def _make_dag(self, scheduler, tool_calls):
        """构造 ToolDAG（使用调度器同一注册表）。"""
        return ToolDAG(tool_calls, scheduler._registry)

    def _make_layers(self, dag):
        """构造 layers 快照（[[tc_id, ...], ...]）。"""
        return dag.topological_sort()

    # ── 单元：_find_next_layer 作用域 ─────────────────────

    def test_find_next_layer_outermost_blocked_by_running_bash(self):
        """最外层调度（is_outermost=True）：bash 运行中，read 工具被独占过滤拦截。"""
        scheduler = ToolScheduler()
        scheduler._reset_global_state()
        scheduler._running_bash_ids.add("bash_1")

        dag = self._make_dag(scheduler, [
            {"id": "ls_1", "name": "ls", "arguments": {"path": "."}},
        ])
        layers = self._make_layers(dag)

        target = scheduler._find_next_layer(dag, layers, is_outermost=True)

        # bash 运行中 + 最外层：非 dispatch_agent 工具被过滤 → 空层（轮询等待）
        assert target == []

    def test_find_next_layer_subagent_not_blocked_by_parent_bash(self):
        """SubAgent 嵌套调度（is_outermost=False）：父 Agent bash 运行中，read 工具放行。"""
        scheduler = ToolScheduler()
        scheduler._reset_global_state()
        scheduler._running_bash_ids.add("bash_1")

        dag = self._make_dag(scheduler, [
            {"id": "ls_1", "name": "ls", "arguments": {"path": "."}},
        ])
        layers = self._make_layers(dag)

        target = scheduler._find_next_layer(dag, layers, is_outermost=False)

        # 嵌套调用跳过独占过滤 → 正常返回待执行层
        assert target == ["ls_1"]

    def test_find_next_layer_no_bash_running(self):
        """无 bash 运行时（最外层/嵌套调用）均不被过滤。"""
        scheduler = ToolScheduler()
        scheduler._reset_global_state()

        dag = self._make_dag(scheduler, [
            {"id": "ls_1", "name": "ls", "arguments": {"path": "."}},
        ])
        layers = self._make_layers(dag)

        assert scheduler._find_next_layer(dag, layers, is_outermost=True) == ["ls_1"]
        assert scheduler._find_next_layer(dag, layers, is_outermost=False) == ["ls_1"]

    # ── 集成：SubAgent 调度不被父 Agent bash 阻塞 ─────────

    @pytest.mark.asyncio
    async def test_subagent_schedule_not_blocked_by_parent_bash(self):
        """主 Agent bash 运行中，SubAgent schedule() 的 read 工具秒级完成（不无限等待）。"""
        scheduler = ToolScheduler()
        scheduler._reset_global_state()
        scheduler._schedule_lock = asyncio.Lock()

        async def slow_bash(*_a, **_kw):
            await asyncio.sleep(10)
            return "bash done"

        slow_func = MagicMock()
        slow_func.execute = AsyncMock(side_effect=slow_bash)

        ls_func = MagicMock()
        ls_func.execute = AsyncMock(return_value="ls result")

        def fake_dispatch(name, arguments, agent=None):
            if name == "bash":
                return slow_func
            if name == "ls":
                return ls_func
            raise ValueError(name)

        with patch.object(scheduler._registry, "dispatch", side_effect=fake_dispatch):
            # 主 Agent 调度 bash（慢，10s）
            main_task = asyncio.create_task(scheduler.schedule(
                [{"id": "bash_1", "name": "bash", "arguments": {"command": "sleep"}}],
                agent_ref=MagicMock(),
            ))
            # 等待 bash 进入 _running_bash_ids
            for _ in range(50):
                if scheduler._running_bash_ids:
                    break
                await asyncio.sleep(0.02)
            assert "bash_1" in scheduler._running_bash_ids

            # SubAgent 调度 ls：修复后不被 bash 独占过滤拦截，秒级完成
            results = await asyncio.wait_for(scheduler.schedule(
                [{"id": "ls_1", "name": "ls", "arguments": {"path": "."}}],
                agent_ref=MagicMock(label="agent-1"),
            ), timeout=3)

            assert len(results) == 1
            assert results[0] == ("ls_1", "ls result", True)

            main_task.cancel()
            try:
                await main_task
            except asyncio.CancelledError:
                pass
