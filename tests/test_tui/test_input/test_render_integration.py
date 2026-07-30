"""test_render_integration — Render 线程 stdin 集成测试。

验证 TuiEngine._drain_queue() 在每帧渲染周期中正确调用
Input.process_events() → read_stdin_once()，
且 stdin 读取在 output_lock 之外执行。
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.tui.input import Input


class TestRenderIntegration:
    """测试 TuiEngine._drain_queue() 与 Input.read_stdin_once() 的集成。"""

    @pytest.fixture
    def mock_input(self, tmp_path: Path) -> Input:
        """创建 mock Input 实例。"""
        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            inp = Input(fd=fd, history_file=tmp_path / "history")
            yield inp
        finally:
            os.close(fd)

    def test_drain_queue_calls_process_events(self, mock_input: Input) -> None:
        """验证 TuiEngine._drain_queue() 调用 Input.process_events()。

        在真实的 engine 环境中，_drain_queue 的 _phase_process_input()
        调用 Input.process_events() → read_stdin_once()。
        此测试验证该调用链完整。
        """
        from src.tui._renderer import TuiEngine

        # 创建 engine 实例（最小 mock）
        mock_renderer = MagicMock()
        mock_bb = MagicMock()
        mock_bb.is_active = False
        mock_bb.get_cursor_info.return_value = ("", 0, 0, 0)
        mock_bb.compute_cursor_position.return_value = (1, 1)
        mock_bb.sync_bottom_lines = MagicMock()

        engine = TuiEngine(
            renderer=mock_renderer,
            bottom_bar=mock_bb,
            input_instance=mock_input,
        )

        # 注入 mock input 并验证 _phase_process_input 调用链
        mock_input.process_events = MagicMock()
        engine._input = mock_input

        # 直接调用 _phase_process_input 验证
        engine._phase_process_input()
        mock_input.process_events.assert_called_once()

    def test_drain_queue_without_input_no_crash(self) -> None:
        """验证 TuiEngine._drain_queue() 在无 _input 时不崩溃（向后兼容）。"""
        from src.tui._renderer import TuiEngine

        mock_renderer = MagicMock()
        mock_bb = MagicMock()
        mock_bb.is_active = False
        mock_bb.get_cursor_info.return_value = ("", 0, 0, 0)
        mock_bb.compute_cursor_position.return_value = (1, 1)
        mock_bb.sync_bottom_lines = MagicMock()

        engine = TuiEngine(
            renderer=mock_renderer,
            bottom_bar=mock_bb,
            input_instance=None,  # 无 input
        )

        # 不应抛异常
        engine._phase_process_input()

    def test_process_events_calls_read_stdin_once(self, mock_input: Input, tmp_path: Path) -> None:
        """验证 Input.process_events() 委托 read_stdin_once()。"""
        # process_events 内部调用 read_stdin_once()，用 pipe 验证
        r_fd, w_fd = os.pipe()
        try:
            inp = Input(fd=r_fd, history_file=tmp_path / "test_history")
            os.write(w_fd, b"x")
            time.sleep(0.05)
            inp.process_events()
            assert inp.get_current_text() == "x"
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_stdin_read_outside_output_lock(self) -> None:
        """验证 _phase_process_input() 在 output_lock 之前执行。

        _drain_queue() 方法中 _phase_process_input() 在 _try_acquire_output_lock
        之前调用，确保 stdin 读取不持锁。
        此测试验证代码结构（方法调用顺序）。
        """
        from src.tui._renderer import TuiEngine
        import inspect

        source = inspect.getsource(TuiEngine._drain_queue)
        # _phase_process_input() 应在 _try_acquire_output_lock 之前出现
        phase_pos = source.find("_phase_process_input()")
        lock_pos = source.find("_try_acquire_output_lock")
        assert phase_pos >= 0, "_phase_process_input() 应在 _drain_queue 中"
        assert lock_pos >= 0, "_try_acquire_output_lock 应在 _drain_queue 中"
        assert phase_pos < lock_pos, (
            "_phase_process_input() 应在 _try_acquire_output_lock 之前调用"
        )
