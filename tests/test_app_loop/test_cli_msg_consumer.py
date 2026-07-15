"""Tests for InteractiveLoop._cli_msg_consumer — 异常处理逻辑。

验证致命异常触发 _force_exit，非致命异常不触发。
"""

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock

from src.app_loop._loop import InteractiveLoop
from src.core.message_queue import Message, MessageQueue
from src.core.exceptions import FatalError, NonFatalError, TransientError


# ═══════════════════════════════════════════════════════════
# _cli_msg_consumer 异常处理测试
# ═══════════════════════════════════════════════════════════


class TestCliMsgConsumerExceptionHandling:
    """测试 _cli_msg_consumer 的异常处理逻辑。"""

    @pytest.fixture
    def loop(self):
        """创建 InteractiveLoop 实例。"""
        return InteractiveLoop()

    @pytest.fixture
    def session(self):
        """创建 mock session。"""
        session = MagicMock()
        session.run_round = AsyncMock()
        return session

    @pytest.fixture
    def state(self):
        """创建 mock state。"""
        return MagicMock()

    @pytest.fixture
    def msg_done(self):
        """创建 msg_done 事件。"""
        return asyncio.Event()

    def _make_msg(self, content):
        """创建消息对象。"""
        return Message(content=content)

    @pytest.mark.asyncio
    async def test_fatal_exception_sets_force_exit(self, loop, session, state, msg_done):
        """致命异常应设置 _force_exit。"""
        session.run_round.side_effect = MemoryError("out of memory")

        msg = self._make_msg("test message")

        # 确保 _force_exit 初始状态
        assert not loop._force_exit.is_set()

        await loop._cli_msg_consumer(msg, session, state, msg_done)

        # 致命异常应设置 _force_exit
        assert loop._force_exit.is_set()
        # msg_done 应被设置
        assert msg_done.is_set()

    @pytest.mark.asyncio
    async def test_nonfatal_exception_does_not_set_force_exit(self, loop, session, state, msg_done):
        """非致命异常不应设置 _force_exit。"""
        session.run_round.side_effect = ConnectionError("network error")

        msg = self._make_msg("test message")

        # 确保 _force_exit 初始状态
        assert not loop._force_exit.is_set()

        await loop._cli_msg_consumer(msg, session, state, msg_done)

        # 非致命异常不应设置 _force_exit
        assert not loop._force_exit.is_set()
        # msg_done 应被设置
        assert msg_done.is_set()

    @pytest.mark.asyncio
    async def test_timeout_error_nonfatal(self, loop, session, state, msg_done):
        """TimeoutError 应为非致命异常。"""
        session.run_round.side_effect = TimeoutError("request timeout")

        msg = self._make_msg("test message")

        await loop._cli_msg_consumer(msg, session, state, msg_done)

        assert not loop._force_exit.is_set()
        assert msg_done.is_set()

    @pytest.mark.asyncio
    async def test_value_error_nonfatal(self, loop, session, state, msg_done):
        """ValueError 应为非致命异常。"""
        session.run_round.side_effect = ValueError("invalid value")

        msg = self._make_msg("test message")

        await loop._cli_msg_consumer(msg, session, state, msg_done)

        assert not loop._force_exit.is_set()
        assert msg_done.is_set()

    @pytest.mark.asyncio
    async def test_key_error_nonfatal(self, loop, session, state, msg_done):
        """KeyError 应为非致命异常。"""
        session.run_round.side_effect = KeyError("missing key")

        msg = self._make_msg("test message")

        await loop._cli_msg_consumer(msg, session, state, msg_done)

        assert not loop._force_exit.is_set()
        assert msg_done.is_set()

    @pytest.mark.asyncio
    async def test_custom_fatal_error_sets_force_exit(self, loop, session, state, msg_done):
        """FatalError 应设置 _force_exit。"""
        session.run_round.side_effect = FatalError("data corruption")

        msg = self._make_msg("test message")

        await loop._cli_msg_consumer(msg, session, state, msg_done)

        assert loop._force_exit.is_set()
        assert msg_done.is_set()

    @pytest.mark.asyncio
    async def test_custom_nonfatal_error_does_not_set_force_exit(self, loop, session, state, msg_done):
        """NonFatalError 不应设置 _force_exit。"""
        session.run_round.side_effect = NonFatalError("temporary failure")

        msg = self._make_msg("test message")

        await loop._cli_msg_consumer(msg, session, state, msg_done)

        assert not loop._force_exit.is_set()
        assert msg_done.is_set()

    @pytest.mark.asyncio
    async def test_transient_error_nonfatal(self, loop, session, state, msg_done):
        """TransientError（NonFatalError 子类）不应设置 _force_exit。"""
        session.run_round.side_effect = TransientError("rate limited")

        msg = self._make_msg("test message")

        await loop._cli_msg_consumer(msg, session, state, msg_done)

        assert not loop._force_exit.is_set()
        assert msg_done.is_set()

    @pytest.mark.asyncio
    async def test_os_error_enospc_sets_force_exit(self, loop, session, state, msg_done):
        """OSError ENOSPC (errno 28) 应设置 _force_exit。"""
        session.run_round.side_effect = OSError("[Errno 28] No space left on device")

        msg = self._make_msg("test message")

        await loop._cli_msg_consumer(msg, session, state, msg_done)

        assert loop._force_exit.is_set()
        assert msg_done.is_set()

    @pytest.mark.asyncio
    async def test_os_error_enomem_sets_force_exit(self, loop, session, state, msg_done):
        """OSError ENOMEM (errno 12) 应设置 _force_exit。"""
        session.run_round.side_effect = OSError("[Errno 12] Cannot allocate memory")

        msg = self._make_msg("test message")

        await loop._cli_msg_consumer(msg, session, state, msg_done)

        assert loop._force_exit.is_set()
        assert msg_done.is_set()

    @pytest.mark.asyncio
    async def test_os_error_other_nonfatal(self, loop, session, state, msg_done):
        """其他 OSError（如权限错误）不应设置 _force_exit。"""
        session.run_round.side_effect = OSError("[Errno 13] Permission denied")

        msg = self._make_msg("test message")

        await loop._cli_msg_consumer(msg, session, state, msg_done)

        assert not loop._force_exit.is_set()
        assert msg_done.is_set()

    @pytest.mark.asyncio
    async def test_nonfatal_exception_writes_error_message(self, loop, session, state, msg_done):
        """非致命异常时 chat_ui.write_line 应被调用，包含错误消息。"""
        session.run_round.side_effect = ValueError("invalid value")
        loop._chat_ui = MagicMock()

        msg = self._make_msg("test message")

        await loop._cli_msg_consumer(msg, session, state, msg_done)

        # 验证 write_line 被调用且包含 ⚠ 字符
        loop._chat_ui.write_line.assert_called_once()
        call_args = loop._chat_ui.write_line.call_args[0][0]
        assert "\u26a0" in call_args, f"错误消息应包含 ⚠ 字符，实际: {call_args}"
        assert "invalid value" in call_args, f"错误消息应包含异常描述，实际: {call_args}"
        # 验证 _force_exit 未被设置
        assert not loop._force_exit.is_set()
        # 验证 msg_done 被设置
        assert msg_done.is_set()

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates(self, loop, session, state, msg_done):
        """CancelledError 应重新抛出，不设置 _force_exit。"""
        session.run_round.side_effect = asyncio.CancelledError()

        msg = self._make_msg("test message")

        with pytest.raises(asyncio.CancelledError):
            await loop._cli_msg_consumer(msg, session, state, msg_done)

        # CancelledError 传播，_force_exit 不应设置
        assert not loop._force_exit.is_set()
        # msg_done 应在 finally 中设置
        assert msg_done.is_set()

    @pytest.mark.asyncio
    async def test_msg_done_always_set_on_success(self, loop, session, state, msg_done):
        """正常执行时 msg_done 应被设置。"""
        session.run_round.return_value = {"result": "ok"}

        msg = self._make_msg("test message")

        await loop._cli_msg_consumer(msg, session, state, msg_done)

        assert msg_done.is_set()

    @pytest.mark.asyncio
    async def test_msg_done_set_when_already_set(self, loop, session, state, msg_done):
        """msg_done 已设置时不应报错。"""
        msg_done.set()

        msg = self._make_msg("test message")

        await loop._cli_msg_consumer(msg, session, state, msg_done)

        assert msg_done.is_set()


class TestCliMsgConsumerWithRetrySentinel:
    """测试 _cli_msg_consumer 处理 RETRY_SENTINEL。"""

    @pytest.fixture
    def loop(self):
        return InteractiveLoop()

    @pytest.fixture
    def session(self):
        session = MagicMock()
        return session

    @pytest.fixture
    def state(self):
        return MagicMock()

    @pytest.fixture
    def msg_done(self):
        return asyncio.Event()

    @pytest.mark.asyncio
    async def test_retry_sentinel_fatal_exception(self, loop, session, state, msg_done):
        """RETRY_SENTINEL 处理中的致命异常应设置 _force_exit。"""
        from src.app_loop._utils import _RETRY_SENTINEL

        with patch("src.app_loop._loop._handle_retry_sentinel", new_callable=AsyncMock) as mock_handler:
            mock_handler.side_effect = MemoryError("out of memory")

            msg = Message(content=_RETRY_SENTINEL)

            await loop._cli_msg_consumer(msg, session, state, msg_done)

            assert loop._force_exit.is_set()
            assert msg_done.is_set()

    @pytest.mark.asyncio
    async def test_retry_sentinel_nonfatal_exception(self, loop, session, state, msg_done):
        """RETRY_SENTINEL 处理中的非致命异常不应设置 _force_exit。"""
        from src.app_loop._utils import _RETRY_SENTINEL

        with patch("src.app_loop._loop._handle_retry_sentinel", new_callable=AsyncMock) as mock_handler:
            mock_handler.side_effect = ConnectionError("network error")

            msg = Message(content=_RETRY_SENTINEL)

            await loop._cli_msg_consumer(msg, session, state, msg_done)

            assert not loop._force_exit.is_set()
            assert msg_done.is_set()


class TestCliMsgConsumerWithCommand:
    """测试 _cli_msg_consumer 处理命令消息。"""

    @pytest.fixture
    def loop(self):
        return InteractiveLoop()

    @pytest.fixture
    def session(self):
        session = MagicMock()
        return session

    @pytest.fixture
    def state(self):
        return MagicMock()

    @pytest.fixture
    def msg_done(self):
        return asyncio.Event()

    @pytest.mark.asyncio
    async def test_command_fatal_exception(self, loop, session, state, msg_done):
        """命令处理中的致命异常应设置 _force_exit。"""
        with patch.object(loop, "_handle_command_msg", new_callable=AsyncMock) as mock_handler:
            mock_handler.side_effect = FatalError("critical failure")

            msg = Message(content="/test")

            await loop._cli_msg_consumer(msg, session, state, msg_done)

            assert loop._force_exit.is_set()
            assert msg_done.is_set()

    @pytest.mark.asyncio
    async def test_command_nonfatal_exception(self, loop, session, state, msg_done):
        """命令处理中的非致命异常不应设置 _force_exit。"""
        with patch.object(loop, "_handle_command_msg", new_callable=AsyncMock) as mock_handler:
            mock_handler.side_effect = ValueError("invalid argument")

            msg = Message(content="/test")

            await loop._cli_msg_consumer(msg, session, state, msg_done)

            assert not loop._force_exit.is_set()
            assert msg_done.is_set()
