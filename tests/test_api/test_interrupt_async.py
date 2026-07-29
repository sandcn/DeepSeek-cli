"""interrupt_async 参数化重构后的单元测试。

验证：
  - flush_stdin(input_instance) 策略模式：有 Input 委托，无 Input 走旧路径
  - reset_interrupt_async(input_instance) 参数透传
  - 向后兼容：无参调用行为不变
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from src.api.interrupt_async import (
    flush_stdin,
    reset_interrupt_async,
    _interrupted,
)


# ── TestFlushStdinParameterized ──────────────────────────────────

class TestFlushStdinParameterized:
    """flush_stdin(input_instance) 参数化测试。"""

    def setup_method(self):
        """每个测试前清除中断标志。"""
        _interrupted.clear()

    def test_no_arg_calls_original_path(self):
        """无参调用应走旧路径（直接操作 sys.stdin）。"""
        with patch("src.api.interrupt_async.select.select") as mock_select:
            mock_select.return_value = ([], [], [])
            # 不应抛出异常
            flush_stdin()
            mock_select.assert_called()

    def test_input_instance_delegates_to_flush_stdin_buffer(self):
        """传入有 flush_stdin_buffer 方法的 Input 实例时应委托。"""
        mock_input = MagicMock()
        mock_input.flush_stdin_buffer = MagicMock()
        flush_stdin(input_instance=mock_input)
        mock_input.flush_stdin_buffer.assert_called_once()

    def test_input_instance_without_method_falls_back(self):
        """传入没有 flush_stdin_buffer 方法的对象时应走旧路径。"""
        mock_input = MagicMock(spec=[])  # 无 flush_stdin_buffer 方法
        with patch("src.api.interrupt_async.select.select") as mock_select:
            mock_select.return_value = ([], [], [])
            flush_stdin(input_instance=mock_input)
            mock_select.assert_called()

    def test_none_input_falls_back(self):
        """传入 None 时应走旧路径。"""
        with patch("src.api.interrupt_async.select.select") as mock_select:
            mock_select.return_value = ([], [], [])
            flush_stdin(input_instance=None)
            mock_select.assert_called()


# ── TestResetInterruptAsyncParameterized ──────────────────────────

class TestResetInterruptAsyncParameterized:
    """reset_interrupt_async(input_instance) 参数化测试。"""

    def setup_method(self):
        """每个测试前设置中断标志。"""
        _interrupted.set()

    def test_clears_interrupt_flag(self):
        """应清除 _interrupted 标志。"""
        assert _interrupted.is_set()
        with patch("src.api.interrupt_async.flush_stdin"):
            reset_interrupt_async()
        assert not _interrupted.is_set()

    def test_passes_input_instance_to_flush_stdin(self):
        """input_instance 参数应透传给 flush_stdin()。"""
        mock_input = MagicMock()
        with patch("src.api.interrupt_async.flush_stdin") as mock_flush:
            reset_interrupt_async(input_instance=mock_input)
            mock_flush.assert_called_once_with(mock_input)

    def test_no_arg_passes_none_to_flush_stdin(self):
        """无参调用时 flush_stdin 收到 None。"""
        with patch("src.api.interrupt_async.flush_stdin") as mock_flush:
            reset_interrupt_async()
            mock_flush.assert_called_once_with(None)

    def test_backward_compatible_no_args(self):
        """向后兼容：无参调用不应抛出异常。"""
        with patch("src.api.interrupt_async.flush_stdin"):
            # 不应抛出异常
            reset_interrupt_async()
