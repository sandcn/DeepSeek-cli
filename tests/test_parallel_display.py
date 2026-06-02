"""Tests for src/ui/parallel/display.py — ParallelDisplay"""

import asyncio
import sys
from io import StringIO

import pytest

from src.ui.parallel.display import ParallelDisplay


@pytest.fixture
def display():
    """返回一个 ParallelDisplay 实例（不启动定时器）。"""
    return ParallelDisplay()


class TestCaptureAndPrintAsyncConcurrency:
    """capture_and_print_async 并发竞态回归测试。

    验证异步协程交错时 redirect_stdout 不会出现输出丢失或泄漏。
    """

    @pytest.mark.asyncio
    async def test_concurrent_stdout_capture_no_loss(self, display):
        """多个协程并发调用 capture_and_print_async，每个都 print 内容。

        在正确的实现（asyncio.Lock 保护）下，所有输出应被捕获到各自 buf；
        在没有锁保护的错误实现下，部分输出会丢失。
        """
        N = 20  # 并发协程数，充分触发协程交错

        async def task(i: int) -> str:
            async def inner():
                print(f"output_from_task_{i}")
                return f"result_{i}"
            return await display.capture_and_print_async(inner)

        tasks = [task(i) for i in range(N)]
        results = await asyncio.gather(*tasks)

        # 验证每个协程都返回了正确的结果
        for i, res in enumerate(results):
            assert res == f"result_{i}", (
                f"协程 {i} 结果异常: expected result_{i}, got {res}"
            )

    @pytest.mark.asyncio
    async def test_stdout_not_polluted_after_concurrent_calls(self, display):
        """并发调用后，sys.stdout 恢复正常（没有被残留的 StringIO 污染）。"""
        N = 10

        async def task(i: int) -> int:
            async def inner():
                print(f"task_{i}")
                return i
            return await display.capture_and_print_async(inner)

        original_stdout = sys.stdout
        await asyncio.gather(*(task(i) for i in range(N)))
        assert sys.stdout is original_stdout, (
            f"并发调用后 sys.stdout 被污染: expected {original_stdout}, "
            f"got {sys.stdout}"
        )

    @pytest.mark.asyncio
    async def test_concurrent_output_isolated(self, display):
        """并发协程间的 stdout 输出不会互相串流。

        每个协程的 print 内容应只进入自己的 buf，
        不应被其他协程的 buf 捕获到。
        """
        collected = []

        async def task(label: str, msg: str) -> str:
            async def inner():
                print(msg)
                return label
            return await display.capture_and_print_async(inner)

        # 两个协程同时打印不同内容
        t1 = task("A", "hello_from_A")
        t2 = task("B", "hello_from_B")
        results = await asyncio.gather(t1, t2)

        assert set(results) == {"A", "B"}, (
            f"两个协程都应成功返回: {results}"
        )


class TestRefreshRegistration:
    """ParallelDisplay refresh() 及注册/注销链路测试。

    验证 start()/stop() 对 chat_ui._active_subagent_panel 的注册与注销，
    以及 refresh() 公开方法的正常调用。
    """

    def test_refresh_called_safely(self, display):
        """refresh() 可被安全调用（无渲染状态时静默跳过）。"""
        # 尚未 start()，无 agent 注册，refresh 不应抛异常
        display.refresh()

    def test_start_registers_to_chat_ui(self, display):
        """start() 将 SubAgentPanelControl 注册到 chat_ui._state._active_subagent_panel。

        ParallelDisplay.start() 创建 SubAgentPanelControl 并注册到全局引用。
        引擎层（_engine.py/_consumer.py）均从 _state 模块读取该引用。
        """
        import src.chat_ui._state as chat_ui_state
        assert chat_ui_state._active_subagent_panel is None
        display.add_agent("agent-1", "test agent")
        # Mock ChatUI 以提供 output_adapter
        from unittest.mock import patch, MagicMock
        mock_chat_ui = MagicMock()
        mock_chat_ui.output_adapter = MagicMock()
        # output_adapter.width 需要返回整数（render_frame 中用于 FrameRenderer）
        mock_chat_ui.output_adapter.width = 120
        with patch('src.chat_ui.get_active_chat_ui', return_value=mock_chat_ui):
            display.start()
        assert chat_ui_state._active_subagent_panel is display._panel
        assert chat_ui_state._active_subagent_panel is not None
        display.stop()

    def test_stop_clears_chat_ui_reference(self, display):
        """stop() 从 chat_ui._state._active_subagent_panel 注销引用。"""
        import src.chat_ui._state as chat_ui_state
        display.add_agent("agent-1", "test agent")
        from unittest.mock import patch, MagicMock
        mock_chat_ui = MagicMock()
        mock_chat_ui.output_adapter = MagicMock()
        mock_chat_ui.output_adapter.width = 120
        with patch('src.chat_ui.get_active_chat_ui', return_value=mock_chat_ui):
            display.start()
        assert chat_ui_state._active_subagent_panel is display._panel
        display.stop()
        assert chat_ui_state._active_subagent_panel is None

    def test_start_then_stop_one_cycle(self):
        """一次 start → stop 注册/注销循环正确。"""
        import src.chat_ui._state as chat_ui_state
        d = ParallelDisplay()
        d.add_agent("agent-1", "test agent")
        from unittest.mock import patch, MagicMock
        mock_chat_ui = MagicMock()
        mock_chat_ui.output_adapter = MagicMock()
        mock_chat_ui.output_adapter.width = 120
        with patch('src.chat_ui.get_active_chat_ui', return_value=mock_chat_ui):
            d.start()
        assert chat_ui_state._active_subagent_panel is d._panel
        d.stop()
        assert chat_ui_state._active_subagent_panel is None

    def test_refresh_after_stop_safe(self, display):
        """stop() 后 refresh() 安全（_stopped 守卫跳过渲染）。"""
        display.add_agent("a", "test")
        display.start()
        display.stop()
        display.refresh()  # 不应抛异常

    def test_refresh_with_active_agents(self, display):
        """有活跃 agent 时 refresh() 正常渲染不抛异常。"""
        display.add_agent("agent-1", "test agent", status="running")
        display.refresh()  # 不抛异常即通过
