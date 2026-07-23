"""_drain_stdin_residual 单元测试 — 验证多轮 stdin 排空行为。

测试策略：
  直接测试 _drain_stdin_residual() 函数，使用 unittest.mock.patch 模拟
  select.select 和 os.read 的行为，验证不同 stdin 状态下的排空逻辑。
  不涉及实际终端 I/O。
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch, MagicMock, call

from src.tui.widgets.bottom_bar.selection import _drain_stdin_residual


class TestDrainStdinResidual(unittest.TestCase):
    """验证 _drain_stdin_residual 在各种场景下的行为。"""

    # ── 场景 1：空 stdin（无残余字节）──

    @patch("select.select", return_value=([], [], []))
    @patch("os.read")
    @patch("src._compat_termios.termios.tcflush")
    def test_empty_stdin_all_rounds_select_not_ready(
        self, mock_tcflush, mock_read, mock_select,
    ):
        """空 stdin：所有 3 轮 select 均无数据就绪 → 无 os.read 调用，每轮均 tcflush。"""
        _drain_stdin_residual(0)
        self.assertEqual(mock_select.call_count, 3, "应执行 3 轮 select")
        mock_read.assert_not_called()
        self.assertEqual(mock_tcflush.call_count, 3, "每轮后均应 tcflush")

    @patch("select.select", return_value=([], [], []))
    @patch("os.read")
    @patch("src._compat_termios.termios.tcflush")
    def test_empty_stdin_exhausts_three_rounds(
        self, mock_tcflush, mock_read, mock_select,
    ):
        """空 stdin：3 轮 select 全部无数据，函数正常返回。"""
        _drain_stdin_residual(0)
        self.assertEqual(mock_select.call_count, 3)
        mock_read.assert_not_called()

    # ── 场景 2：stdin 有残余字节 ——

    @patch("select.select", side_effect=[
        ([0], [], []),   # 第 1 轮：有数据
        ([], [], []),     # 第 2 轮：无数据
        ([], [], []),     # 第 3 轮：无数据
    ])
    @patch("os.read", return_value=b"\x1b\x1b\x0a")
    @patch("src._compat_termios.termios.tcflush")
    def test_stdin_with_residual_bytes_read_once(
        self, mock_tcflush, mock_read, mock_select,
    ):
        """stdin 有残余字节：第 1 轮读取，后 2 轮无数据，全部排空。"""
        _drain_stdin_residual(0)
        mock_read.assert_called_once_with(0, 4096)
        self.assertEqual(mock_tcflush.call_count, 3)

    @patch("select.select", side_effect=[
        ([0], [], []),   # 第 1 轮：有数据
        ([0], [], []),   # 第 2 轮：有数据
        ([0], [], []),   # 第 3 轮：有数据
    ])
    @patch("os.read", return_value=b"\x0a")
    @patch("src._compat_termios.termios.tcflush")
    def test_stdin_with_residual_bytes_all_rounds(
        self, mock_tcflush, mock_read, mock_select,
    ):
        """stdin 有残余字节：所有 3 轮均有数据，每轮均读取并 tcflush。"""
        _drain_stdin_residual(0)
        self.assertEqual(mock_read.call_count, 3)
        self.assertEqual(mock_tcflush.call_count, 3)

    # ── 场景 3：fd 无效（closed）→ 不抛异常 ──

    @patch("select.select", side_effect=ValueError("bad fd"))
    @patch("os.read")
    @patch("src._compat_termios.termios.tcflush")
    def test_invalid_fd_select_raises_value_error(
        self, mock_tcflush, mock_read, mock_select,
    ):
        """fd 无效：select 抛 ValueError → 不抛异常，正常返回。"""
        try:
            _drain_stdin_residual(-1)
        except Exception as e:
            self.fail(f"_drain_stdin_residual 不应抛异常: {e}")

    @patch("select.select", return_value=([0], [], []))
    @patch("os.read", side_effect=OSError(9, "Bad file descriptor"))
    @patch("src._compat_termios.termios.tcflush")
    def test_invalid_fd_read_raises_os_error(
        self, mock_tcflush, mock_read, mock_select,
    ):
        """fd 无效：os.read 抛 OSError → 不抛异常，正常返回。"""
        try:
            _drain_stdin_residual(-1)
        except Exception as e:
            self.fail(f"_drain_stdin_residual 不应抛异常: {e}")

    # ── 场景 4：tcflush 失败 → 降级行为 ──

    @patch("select.select", return_value=([], [], []))
    @patch("os.read")
    @patch("src._compat_termios.termios.tcflush",
           side_effect=Exception("tcflush failed"))
    def test_tcflush_fails_silently(
        self, mock_tcflush, mock_read, mock_select,
    ):
        """tcflush 失败：静默降级，不影响 3 轮循环。"""
        try:
            _drain_stdin_residual(0)
        except Exception as e:
            self.fail(f"_drain_stdin_residual 不应抛异常: {e}")
        self.assertEqual(mock_select.call_count, 3,
                         "tcflush 失败不应中断 3 轮循环")

    # ── 场景 5：超时行为验证 ──

    @patch("select.select", return_value=([], [], []))
    @patch("os.read")
    @patch("src._compat_termios.termios.tcflush")
    def test_timeout_per_round_used_correctly(
        self, mock_tcflush, mock_read, mock_select,
    ):
        """每轮使用正确的超时时间（默认 20ms）。"""
        _drain_stdin_residual(0)
        for call_args in mock_select.call_args_list:
            self.assertEqual(call_args[0][3], 0.02,
                             "每轮 select 超时应为 20ms")

    @patch("select.select", return_value=([], [], []))
    @patch("os.read")
    @patch("src._compat_termios.termios.tcflush")
    def test_custom_timeout_rounds_and_max_per_round(
        self, mock_tcflush, mock_read, mock_select,
    ):
        """自定义参数：timeout_per_round=0.05, rounds=2, max_per_round=1024。"""
        _drain_stdin_residual(0, timeout_per_round=0.05, rounds=2, max_per_round=1024)
        self.assertEqual(mock_select.call_count, 2, "应执行 2 轮 select")
        for call_args in mock_select.call_args_list:
            self.assertEqual(call_args[0][3], 0.05,
                             "每轮 select 超时应为 50ms")


class TestDrainStdinResidualEdgeCases(unittest.TestCase):
    """验证 _drain_stdin_residual 的边界情况。"""

    @patch("select.select", return_value=([], [], []))
    @patch("os.read")
    @patch("src._compat_termios.termios.tcflush")
    def test_zero_rounds_no_operations(
        self, mock_tcflush, mock_read, mock_select,
    ):
        """rounds=0：不执行任何操作。"""
        _drain_stdin_residual(0, rounds=0)
        mock_select.assert_not_called()
        mock_read.assert_not_called()
        mock_tcflush.assert_not_called()

    @patch("select.select", return_value=([], [], []))
    @patch("os.read")
    @patch("src._compat_termios.termios.tcflush")
    def test_one_round_single_iteration(
        self, mock_tcflush, mock_read, mock_select,
    ):
        """rounds=1：仅执行 1 轮 select + tcflush。"""
        _drain_stdin_residual(0, rounds=1)
        mock_select.assert_called_once()
        mock_read.assert_not_called()
        mock_tcflush.assert_called_once()

    @patch("select.select", side_effect=[
        ([0], [], []),
        ValueError("intermittent"),
        ([], [], []),
    ])
    @patch("os.read", return_value=b"\x00")
    @patch("src._compat_termios.termios.tcflush")
    def test_intermittent_select_error(
        self, mock_tcflush, mock_read, mock_select,
    ):
        """select 间歇性异常：第 2 轮抛异常 → continue 跳过该轮→继续下一轮。"""
        try:
            _drain_stdin_residual(0)
        except Exception as e:
            self.fail(f"_drain_stdin_residual 不应抛异常: {e}")
        # 第 1 轮：select 就绪 → read → tcflush
        # 第 2 轮：select 抛 ValueError → pass（跳过 read，仍执行 tcflush）
        # 第 3 轮：select 未就绪 → 仅 tcflush
        self.assertEqual(mock_read.call_count, 1, "仅第 1 轮应 read")
        # 每轮后均执行 tcflush（含 select 异常轮）
        self.assertEqual(mock_tcflush.call_count, 3,
                         "第 2 轮 select 异常时 pass，仍应执行 tcflush")
