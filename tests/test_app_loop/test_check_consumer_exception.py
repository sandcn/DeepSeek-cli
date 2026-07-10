"""Tests for InteractiveLoop._check_consumer_exception — 异常处理逻辑。

验证任务取消不触发 _force_exit，致命异常触发 _force_exit，非致命异常不触发。
"""

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from src.app_loop._loop import InteractiveLoop
from src.core.exceptions import FatalError, NonFatalError, TransientError


# ═══════════════════════════════════════════════════════════
# _check_consumer_exception 异常处理测试
# ═══════════════════════════════════════════════════════════


class TestCheckConsumerExceptionHandling:
    """测试 _check_consumer_exception 的异常处理逻辑。"""

    @pytest.fixture
    def loop(self):
        """创建 InteractiveLoop 实例。"""
        loop = InteractiveLoop()
        msg_done = asyncio.Event()
        loop._msg_done_ref = msg_done
        return loop, msg_done

    def _make_task(self, done=True, cancelled=False, exception=None):
        """创建 mock task 对象。"""
        task = MagicMock()
        task.done.return_value = done
        task.cancelled.return_value = cancelled
        if exception is not None:
            task.exception.return_value = exception
        else:
            task.exception.return_value = None
        return task

    def test_task_not_done_returns_early(self):
        """任务未完成时应直接返回。"""
        loop = InteractiveLoop()
        task = self._make_task(done=False)

        # 不应有任何副作用
        loop._check_consumer_exception(task)

        assert not loop._force_exit.is_set()

    def test_task_cancelled_does_not_set_force_exit(self, loop):
        """任务取消不应设置 _force_exit。"""
        loop, msg_done = loop
        task = self._make_task(done=True, cancelled=True)

        loop._check_consumer_exception(task)

        # 任务取消不应设置 _force_exit
        assert not loop._force_exit.is_set()
        # msg_done 应被设置
        assert msg_done.is_set()

    def test_task_cancelled_logs_info(self, loop):
        """任务取消应记录 INFO 日志。"""
        loop, msg_done = loop
        task = self._make_task(done=True, cancelled=True)

        with patch("src.app_loop._loop._logger") as mock_logger:
            loop._check_consumer_exception(task)
            mock_logger.info.assert_called_once_with("消息队列消费者任务被取消")

    def test_fatal_exception_sets_force_exit(self, loop):
        """致命异常应设置 _force_exit。"""
        loop, msg_done = loop
        exc = MemoryError("out of memory")
        task = self._make_task(done=True, exception=exc)

        loop._check_consumer_exception(task)

        # 致命异常应设置 _force_exit
        assert loop._force_exit.is_set()
        # msg_done 应被设置
        assert msg_done.is_set()

    def test_fatal_exception_logs_critical(self, loop):
        """致命异常应记录 CRITICAL 日志。"""
        loop, msg_done = loop
        exc = MemoryError("out of memory")
        task = self._make_task(done=True, exception=exc)

        with patch("src.app_loop._loop._logger") as mock_logger:
            loop._check_consumer_exception(task)
            mock_logger.critical.assert_called_once()

    def test_nonfatal_exception_does_not_set_force_exit(self, loop):
        """非致命异常不应设置 _force_exit。"""
        loop, msg_done = loop
        exc = ConnectionError("network error")
        task = self._make_task(done=True, exception=exc)

        loop._check_consumer_exception(task)

        # 非致命异常不应设置 _force_exit
        assert not loop._force_exit.is_set()
        # msg_done 应被设置
        assert msg_done.is_set()

    def test_nonfatal_exception_logs_warning(self, loop):
        """非致命异常应记录 WARNING 日志。"""
        loop, msg_done = loop
        exc = ConnectionError("network error")
        task = self._make_task(done=True, exception=exc)

        with patch("src.app_loop._loop._logger") as mock_logger:
            loop._check_consumer_exception(task)
            mock_logger.warning.assert_called_once()

    def test_timeout_error_nonfatal(self, loop):
        """TimeoutError 应为非致命异常。"""
        loop, msg_done = loop
        exc = TimeoutError("request timeout")
        task = self._make_task(done=True, exception=exc)

        loop._check_consumer_exception(task)

        assert not loop._force_exit.is_set()
        assert msg_done.is_set()

    def test_value_error_nonfatal(self, loop):
        """ValueError 应为非致命异常。"""
        loop, msg_done = loop
        exc = ValueError("invalid value")
        task = self._make_task(done=True, exception=exc)

        loop._check_consumer_exception(task)

        assert not loop._force_exit.is_set()
        assert msg_done.is_set()

    def test_key_error_nonfatal(self, loop):
        """KeyError 应为非致命异常。"""
        loop, msg_done = loop
        exc = KeyError("missing key")
        task = self._make_task(done=True, exception=exc)

        loop._check_consumer_exception(task)

        assert not loop._force_exit.is_set()
        assert msg_done.is_set()

    def test_custom_fatal_error_sets_force_exit(self, loop):
        """FatalError 应设置 _force_exit。"""
        loop, msg_done = loop
        exc = FatalError("data corruption")
        task = self._make_task(done=True, exception=exc)

        loop._check_consumer_exception(task)

        assert loop._force_exit.is_set()
        assert msg_done.is_set()

    def test_custom_nonfatal_error_does_not_set_force_exit(self, loop):
        """NonFatalError 不应设置 _force_exit。"""
        loop, msg_done = loop
        exc = NonFatalError("temporary failure")
        task = self._make_task(done=True, exception=exc)

        loop._check_consumer_exception(task)

        assert not loop._force_exit.is_set()
        assert msg_done.is_set()

    def test_transient_error_nonfatal(self, loop):
        """TransientError（NonFatalError 子类）不应设置 _force_exit。"""
        loop, msg_done = loop
        exc = TransientError("rate limited")
        task = self._make_task(done=True, exception=exc)

        loop._check_consumer_exception(task)

        assert not loop._force_exit.is_set()
        assert msg_done.is_set()

    def test_os_error_enospc_sets_force_exit(self, loop):
        """OSError ENOSPC (errno 28) 应设置 _force_exit。"""
        loop, msg_done = loop
        exc = OSError("[Errno 28] No space left on device")
        task = self._make_task(done=True, exception=exc)

        loop._check_consumer_exception(task)

        assert loop._force_exit.is_set()
        assert msg_done.is_set()

    def test_os_error_enomem_sets_force_exit(self, loop):
        """OSError ENOMEM (errno 12) 应设置 _force_exit。"""
        loop, msg_done = loop
        exc = OSError("[Errno 12] Cannot allocate memory")
        task = self._make_task(done=True, exception=exc)

        loop._check_consumer_exception(task)

        assert loop._force_exit.is_set()
        assert msg_done.is_set()

    def test_os_error_other_nonfatal(self, loop):
        """其他 OSError（如权限错误）不应设置 _force_exit。"""
        loop, msg_done = loop
        exc = OSError("[Errno 13] Permission denied")
        task = self._make_task(done=True, exception=exc)

        loop._check_consumer_exception(task)

        assert not loop._force_exit.is_set()
        assert msg_done.is_set()

    def test_no_exception_does_nothing(self, loop):
        """无异常时应直接返回。"""
        loop, msg_done = loop
        task = self._make_task(done=True, exception=None)

        loop._check_consumer_exception(task)

        assert not loop._force_exit.is_set()
        # msg_done 不应被设置（无异常情况）
        assert not msg_done.is_set()

    def test_msg_done_already_set(self, loop):
        """msg_done 已设置时不应报错。"""
        loop, msg_done = loop
        msg_done.set()
        exc = ConnectionError("network error")
        task = self._make_task(done=True, exception=exc)

        loop._check_consumer_exception(task)

        assert not loop._force_exit.is_set()
        assert msg_done.is_set()

    def test_msg_done_ref_none(self):
        """_msg_done_ref 为 None 时不应报错。"""
        loop = InteractiveLoop()
        loop._msg_done_ref = None
        exc = ConnectionError("network error")
        task = self._make_task(done=True, exception=exc)

        # 不应抛出异常
        loop._check_consumer_exception(task)

        assert not loop._force_exit.is_set()

    def test_msg_done_ref_none_on_cancelled(self):
        """任务取消且 _msg_done_ref 为 None 时不应报错。"""
        loop = InteractiveLoop()
        loop._msg_done_ref = None
        task = self._make_task(done=True, cancelled=True)

        # 不应抛出异常
        loop._check_consumer_exception(task)

        assert not loop._force_exit.is_set()


class TestCheckConsumerExceptionInvalidState:
    """测试 _check_consumer_exception 处理 InvalidStateError。"""

    def test_invalid_state_error_returns_early(self):
        """task.exception() 抛出 InvalidStateError 时应直接返回。"""
        loop = InteractiveLoop()
        msg_done = asyncio.Event()
        loop._msg_done_ref = msg_done

        task = MagicMock()
        task.done.return_value = True
        task.cancelled.return_value = False
        task.exception.side_effect = asyncio.InvalidStateError("Task is not done")

        with patch("src.app_loop._loop._logger") as mock_logger:
            loop._check_consumer_exception(task)
            mock_logger.warning.assert_called_once()

        assert not loop._force_exit.is_set()
        # msg_done 不应被设置
        assert not msg_done.is_set()
