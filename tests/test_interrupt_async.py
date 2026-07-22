"""测试 src/api/interrupt_async.py 模块

覆盖内容：
  1. request_interrupt_async → is_interrupted_async 返回 True
  2. reset_interrupt_async（先 set 再 reset）→ is_interrupted_async 返回 False
  3. reset_interrupt_async 调用 _flush_stdin（mock 验证）
  4. is_interrupted() 同步桥接函数行为与 is_interrupted_async() 一致
  5. 多次 request_interrupt_async 幂等（多次 set 后 is_set 仍为 True）
"""

from unittest.mock import patch

import pytest


class TestInterruptAsync:
    """Async 全局中断信号测试"""

    @pytest.fixture(autouse=True)
    def _reset_before_each(self):
        """每个测试前复位全局中断信号，保证测试隔离"""
        from src.api.interrupt_async import _interrupted

        _interrupted.clear()
        yield
        _interrupted.clear()

    # ── 1. request → is_set ─────────────────────────────────

    async def test_request_then_is_set(self):
        """调用 request_interrupt_async 后 is_interrupted_async 返回 True"""
        from src.api.interrupt_async import (
            is_interrupted_async,
            request_interrupt_async,
        )

        request_interrupt_async()
        result = await is_interrupted_async()

        assert result is True

    # ── 2. set → reset → is_set ─────────────────────────────

    async def test_reset_after_set_clears(self):
        """先 set 再 reset，is_interrupted_async 返回 False"""
        from src.api.interrupt_async import (
            is_interrupted_async,
            request_interrupt_async,
            reset_interrupt_async,
        )

        request_interrupt_async()
        assert await is_interrupted_async() is True  # 确认已置位

        with patch("src.api.interrupt_async.flush_stdin"):
            reset_interrupt_async()

        assert await is_interrupted_async() is False

    # ── 3. reset 调用 flush_stdin ──────────────────────────

    async def test_reset_calls_flush_stdin(self):
        """reset_interrupt_async 内部调用 flush_stdin"""
        from src.api.interrupt_async import reset_interrupt_async

        with patch("src.api.interrupt_async.flush_stdin") as mock_flush:
            reset_interrupt_async()

        mock_flush.assert_called_once()

    # ── 4. 同步桥接 is_interrupted() ────────────────────────

    async def test_sync_bridge_matches_async(self):
        """is_interrupted() 同步桥接与 is_interrupted_async() 行为一致"""
        from src.api.interrupt_async import (
            is_interrupted,
            is_interrupted_async,
            request_interrupt_async,
            reset_interrupt_async,
        )

        # 初始状态：两者都返回 False
        assert is_interrupted() is False
        assert await is_interrupted_async() is False

        # 请求中断后：两者都返回 True
        request_interrupt_async()
        assert is_interrupted() is True
        assert await is_interrupted_async() is True

        # 复位后：两者都返回 False
        with patch("src.api.interrupt_async.flush_stdin"):
            reset_interrupt_async()
        assert is_interrupted() is False
        assert await is_interrupted_async() is False

    # ── 5. 多次 request 幂等 ────────────────────────────────

    async def test_request_idempotent(self):
        """多次 request_interrupt_async 后 is_set 仍为 True（幂等）"""
        from src.api.interrupt_async import (
            is_interrupted_async,
            request_interrupt_async,
        )

        # 连续多次 set
        request_interrupt_async()
        request_interrupt_async()
        request_interrupt_async()

        assert await is_interrupted_async() is True

    # ── 6. wait_for_interrupt_async 触发 ─────────────────────

    async def test_wait_for_interrupt_async_triggers(self):
        """wait_for_interrupt_async 在中断信号置位后返回 True"""
        from src.api.interrupt_async import (
            request_interrupt_async,
            wait_for_interrupt_async,
        )

        request_interrupt_async()
        result = await wait_for_interrupt_async(timeout=5.0)

        assert result is True

    # ── 7. wait_for_interrupt_async 超时 ─────────────────────

    async def test_wait_for_interrupt_async_timeout(self):
        """wait_for_interrupt_async 在超时后返回 False"""
        from src.api.interrupt_async import wait_for_interrupt_async

        result = await wait_for_interrupt_async(timeout=0.1)

        assert result is False
