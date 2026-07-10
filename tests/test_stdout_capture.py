"""Tests for src/core/agent.py — sys.stdout 捕获与清理

测试策略：
  CaptureManager 管理 stdout 捕获，Agent 仅做薄委托。
  此测试验证委托方法和 CaptureManager 的集成行为。
"""

import io
import sys
from unittest.mock import MagicMock, patch

import pytest


# 创建 _SharedCapture 模拟类
_SharedCapture = type('_SharedCapture', (io.StringIO,), {})


from src.core.agent import Agent
from src.core.internal.agent._capture_manager import CaptureManager


def _make_minimal_agent():
    """创建最小 Agent 实例，仅保留捕获相关的 CaptureManager。"""
    a = object.__new__(Agent)
    a._capture_mgr = CaptureManager()
    return a


def _is_shared_capture(obj) -> bool:
    """检查对象是否为 SharedCapture 实例。"""
    return type(obj).__name__ in ('SharedCapture', '_SharedCapture')


# ═══════════════════════════════════════════════════════════════
# 测试：_cleanup_capture
# ═══════════════════════════════════════════════════════════════

class TestCleanupCaptureFallback:
    """_cleanup_capture 清理孤立 _SharedCapture"""

    def test_fallback_recovers_orphan_shared_capture(self):
        """检测到孤立 _SharedCapture 并恢复为 sys.__stdout__"""
        agent = _make_minimal_agent()
        original = sys.stdout
        try:
            sys.stdout = _SharedCapture()
            agent._capture_mgr.cleanup()
            assert sys.stdout is sys.__stdout__
        finally:
            sys.stdout = original

    def test_fallback_noop_when_stdout_normal(self):
        """sys.stdout 正常时，清理无副作用"""
        agent = _make_minimal_agent()
        original = sys.stdout
        try:
            agent._capture_mgr.cleanup()
            assert sys.stdout is original
        finally:
            sys.stdout = original


# ═══════════════════════════════════════════════════════════════
# 测试：_start_tool_output_capture 防御
# ═══════════════════════════════════════════════════════════════

class TestStartCaptureStateNoneDefense:
    """_start_tool_output_capture 中 _capture_mgr 的防御处理"""

    def test_state_none_skips_gracefully(self):
        """初始状态为 None 时 start_capture 正常工作"""
        agent = _make_minimal_agent()
        original = sys.stdout
        try:
            with patch('src.ui.events.event_bus.DisplayEventBus') as mb:
                mb.get_default.return_value = MagicMock()
                agent._capture_mgr.start_capture("tool_1")
                assert _is_shared_capture(sys.stdout)
        finally:
            agent._capture_mgr.cleanup()
            sys.stdout = original

    def test_cleanup_then_start_reinitializes(self):
        """cleanup 后重新 start 能正常工作"""
        agent = _make_minimal_agent()
        original = sys.stdout
        try:
            agent._capture_mgr.cleanup()
            with patch('src.ui.events.event_bus.DisplayEventBus') as mb:
                mb.get_default.return_value = MagicMock()
                agent._capture_mgr.start_capture("new_label")
                assert _is_shared_capture(sys.stdout)
        finally:
            agent._capture_mgr.cleanup()
            sys.stdout = original


# ═══════════════════════════════════════════════════════════════
# 测试：完整捕获→释放流程
# ═══════════════════════════════════════════════════════════════

class TestCaptureReleaseCycle:
    """_start → _stop 完整流程"""

    def test_release_restores_stdout(self):
        """捕获后释放，sys.stdout 被正确恢复"""
        agent = _make_minimal_agent()
        original = sys.stdout
        try:
            with patch('src.ui.events.event_bus.DisplayEventBus') as mb:
                mb.get_default.return_value = MagicMock()
                agent._capture_mgr.start_capture("tool_1")
                assert _is_shared_capture(sys.stdout)
                agent._capture_mgr.stop_capture("tool_1")
                assert sys.stdout is original
        finally:
            agent._capture_mgr.cleanup()
            sys.stdout = original

    def test_multi_tool_shared_capture(self):
        """多工具共享捕获：最后一个释放后才恢复 stdout"""
        agent = _make_minimal_agent()
        original = sys.stdout
        try:
            with patch('src.ui.events.event_bus.DisplayEventBus') as mb:
                mb.get_default.return_value = MagicMock()
                agent._capture_mgr.start_capture("tool_1")
                agent._capture_mgr.start_capture("tool_2")
                capture = sys.stdout
                assert _is_shared_capture(capture)
                agent._capture_mgr.stop_capture("tool_1")
                assert sys.stdout is capture
                agent._capture_mgr.stop_capture("tool_2")
                assert sys.stdout is original
        finally:
            agent._capture_mgr.cleanup()
            sys.stdout = original

    def test_release_nonexistent_label_noop(self):
        """释放不存在的 label 无害"""
        agent = _make_minimal_agent()
        original = sys.stdout
        try:
            with patch('src.ui.events.event_bus.DisplayEventBus') as mb:
                mb.get_default.return_value = MagicMock()
                agent._capture_mgr.start_capture("tool_1")
                agent._capture_mgr.stop_capture("nonexistent")
                assert _is_shared_capture(sys.stdout)
                agent._capture_mgr.stop_capture("tool_1")
                assert sys.stdout is original
        finally:
            agent._capture_mgr.cleanup()
            sys.stdout = original


# ═══════════════════════════════════════════════════════════════
# 测试：自修复检测
# ═══════════════════════════════════════════════════════════════

class TestSelfHealing:
    """_start_tool_output_capture 入口处的自修复逻辑"""

    def test_heals_orphan_shared_capture_cleans_state(self):
        """sys.stdout 是孤立 SharedCapture 时自动恢复并重新捕获"""
        agent = _make_minimal_agent()
        original = sys.stdout
        try:
            sys.stdout = _SharedCapture()
            with patch('src.ui.events.event_bus.DisplayEventBus') as mb:
                mb.get_default.return_value = MagicMock()
                agent._capture_mgr.start_capture("tool_1")
                assert not _is_shared_capture(sys.stdout) or \
                       not isinstance(sys.stdout, _SharedCapture), \
                    "自修复后 stdout 不是原始孤立 capture"
        finally:
            agent._capture_mgr.cleanup()
            sys.stdout = original

    def test_heals_orphan_without_state(self):
        """sys.stdout 是孤立 SharedCapture 时自动修复并重新捕获"""
        agent = _make_minimal_agent()
        original = sys.stdout
        try:
            sys.stdout = _SharedCapture()
            with patch('src.ui.events.event_bus.DisplayEventBus') as mb:
                mb.get_default.return_value = MagicMock()
                agent._capture_mgr.start_capture("tool_1")
                # 自修复后重新建立捕获，stdout 应为新的 SharedCapture
                assert _is_shared_capture(sys.stdout), \
                    "自修复后 stdout 应为新的 SharedCapture（正在捕获）"
                assert not isinstance(sys.stdout, _SharedCapture), \
                    "stdout 不是原来的孤立 capture"
        finally:
            agent._capture_mgr.cleanup()
            sys.stdout = original
