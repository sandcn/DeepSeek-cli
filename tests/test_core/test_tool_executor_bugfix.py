"""测试 B7 修复：FIRST_EXCEPTION 路径中已完成 pending task 结果丢失。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.tool_executor_async import ToolScheduler


class TestB7PendingTaskResultCapture:
    """B7 修复：FIRST_EXCEPTION 路径中已完成 pending task 结果保存"""

    @pytest.mark.asyncio
    async def test_execute_concurrent_returns_results(self):
        """_execute_concurrent 正常路径返回所有工具结果"""
        scheduler = ToolScheduler()
        scheduler._reset_global_state()
        scheduler._schedule_lock = asyncio.Lock()

        tool_calls = [
            {"id": "call_1", "name": "ls", "arguments": {"path": "."}},
        ]

        mock_func = AsyncMock()
        mock_func.execute = AsyncMock(return_value="result_1")

        with patch.object(scheduler._registry, 'dispatch', return_value=mock_func):
            results = await scheduler._execute_concurrent(
                tool_calls, agent_ref=MagicMock(),
            )

        assert len(results) == 1
        assert results[0][0] == "call_1"

    @pytest.mark.asyncio
    async def test_execute_one_async_success(self):
        """单个成功工具执行返回正确结果"""
        scheduler = ToolScheduler()
        tc = {"id": "call_1", "name": "ls", "arguments": {"path": "."}}

        mock_func = AsyncMock()
        mock_func.execute = AsyncMock(return_value="success_output")

        with patch.object(scheduler._registry, 'dispatch', return_value=mock_func):
            result = await scheduler._execute_one_async(
                tc, agent_ref=MagicMock(),
                on_before=None, on_after=None, run_method=None,
            )

        assert result[0] == "call_1"
        assert result[1] == "success_output"
        assert result[2] is True

    @pytest.mark.asyncio
    async def test_execute_one_async_failure(self):
        """单个失败工具执行返回错误结果"""
        scheduler = ToolScheduler()
        tc = {"id": "call_1", "name": "ls", "arguments": {"path": "."}}

        mock_func = AsyncMock()
        mock_func.execute = AsyncMock(side_effect=ValueError("test error"))

        with patch.object(scheduler._registry, 'dispatch', return_value=mock_func):
            result = await scheduler._execute_one_async(
                tc, agent_ref=MagicMock(),
                on_before=None, on_after=None, run_method=None,
            )

        assert result[0] == "call_1"
        assert "失败" in result[1]
        assert result[2] is False
