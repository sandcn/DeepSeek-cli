"""测试 BUG-A1 修复：取消路径下 _do_terminal_output / publish_summary 去重。

覆盖：
  - 取消路径直接调用各 1 次，finally 跳过（cancel_output_done 标志）
  - AgentResultEvent 发布总数 == len(specs)（不重复发布）
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.parallel_executor import ParallelExecutor


def _make_display() -> MagicMock:
    display = MagicMock()
    display.await_stop = AsyncMock()
    return display


class TestCancelNoDuplicateOutput:
    """BUG-A1：取消路径不重复输出/发布。"""

    @pytest.mark.asyncio
    async def test_cancel_no_duplicate_output_regression(self) -> None:
        """触发 CancelledError：_do_terminal_output / publish_summary 各恰好 1 次。"""
        executor = ParallelExecutor(MagicMock())
        specs = [{"description": "t1", "prompt": "p1"}]

        async def _cancelled():
            raise asyncio.CancelledError

        with patch.object(executor, "_do_terminal_output", return_value=None) as mock_out, \
             patch.object(executor._spawner, "publish_summary", return_value=None) as mock_pub:
            with pytest.raises(asyncio.CancelledError):
                await executor._execute_with_error_handling(
                    _cancelled(), specs, _make_display(), is_batch=True,
                )

        # 取消路径直接调用 1 次 + finally 跳过（cancel_output_done=True）→ 共 1 次
        assert mock_out.call_count == 1
        assert mock_pub.call_count == 1

    @pytest.mark.asyncio
    async def test_agent_result_event_once_regression(self) -> None:
        """捕获 EventBus 发布：AgentResultEvent 总数 == len(specs)。"""
        executor = ParallelExecutor(MagicMock())
        specs = [
            {"description": "t1", "prompt": "p1"},
            {"description": "t2", "prompt": "p2"},
        ]
        mock_port = MagicMock()
        executor._spawner._event_port = mock_port

        async def _cancelled():
            raise asyncio.CancelledError

        with patch.object(executor, "_do_terminal_output", return_value=None):
            with pytest.raises(asyncio.CancelledError):
                await executor._execute_with_error_handling(
                    _cancelled(), specs, _make_display(), is_batch=True,
                )

        # AgentResultEvent 发布总数 == len(specs)（仅取消路径发布一次）
        assert mock_port.publish_event.call_count == len(specs)
