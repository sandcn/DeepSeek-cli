"""测试 BUG-A2 修复：register_and_wait barrier 超时兜底防死锁。

覆盖：
  - 注册 1/3 后等待超时：不永久阻塞且 _execute_all 被触发
  - 正常 3/3 注册：最后一个注册触发 _execute_all（原路径无变化）
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.parallel_executor import ParallelExecutor


class TestBarrierTimeout:
    """BUG-A2：register_and_wait 超时兜底。"""

    @pytest.mark.asyncio
    async def test_barrier_timeout_no_deadlock_regression(self) -> None:
        """注册 1/3 后 mock wait 超时：不永久阻塞且 _execute_all 被触发。"""
        executor = ParallelExecutor(MagicMock())
        executor.setup_barrier(3)
        # 只有 1 个协程注册（其余 2 个在 register_and_wait 前抛异常）
        async with executor._agents_lock:
            executor._registered_count = 1
        # deadline 已过期 → wait(timeout=0) 立即超时
        executor._barrier_deadline = time.monotonic() - 1.0

        async def _mock_execute_all():
            executor._all_done.set()
            return []

        with patch.object(ParallelExecutor, "_execute_all",
                          new=AsyncMock(side_effect=_mock_execute_all)) as mock_exec:
            await executor.register_and_wait()
            mock_exec.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_barrier_all_registered_no_timeout_regression(self) -> None:
        """正常 3/3 注册走原路径（最后一个注册触发 _execute_all）。"""
        executor = ParallelExecutor(MagicMock())
        executor.setup_barrier(3)

        async def _mock_execute_all():
            executor._all_done.set()
            return []

        async def _reg():
            await executor.register_and_wait()

        with patch.object(ParallelExecutor, "_execute_all",
                          new=AsyncMock(side_effect=_mock_execute_all)) as mock_exec:
            await asyncio.gather(_reg(), _reg(), _reg())
            mock_exec.assert_awaited_once()
