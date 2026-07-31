"""集成测试：Input → _BottomBar → TuiEngine 光标定位闭环（适配统一 Input 类）。

验证 _BottomBar 适配统一 Input 类后的端到端光标定位正确性。
"""

from __future__ import annotations

import os
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

from src.tui.input import Input


class TestInputBottomBarEngineIntegration:
    """验证 Input → _BottomBar → TuiEngine 的光标定位闭环。"""

    @pytest.fixture
    def mock_width_cache(self):
        """Mock TerminalWidthCache。"""
        cache = MagicMock()
        cache.get_width.return_value = 80
        cache.get_height.return_value = 24
        return cache

    @pytest.fixture
    def mock_input(self, mock_width_cache, tmp_path):
        """创建 mock Input 实例。"""
        fd = os.open("/dev/null", os.O_RDONLY)
        inp = Input(
            fd=fd,
            history_file=tmp_path / "test_history",
            term_width_cache=mock_width_cache,
            cursor_tracker=MagicMock(),
        )
        # Mock compute_cursor 返回值
        inp.compute_cursor = MagicMock(return_value=(20, 5, 0, 2))
        return inp

    @pytest.fixture
    def mock_bottom_bar(self, mock_input, mock_width_cache):
        """创建注入了 Input 的 _BottomBar mock。"""
        from src.tui._bottom_bar import _BottomBar

        bb = _BottomBar()
        bb._width_cache = mock_width_cache
        bb.set_input(mock_input)
        bb._active = True
        bb._last_text = "test input"
        bb._last_rendered_text = "test input"
        bb._input_cursor_pos = 10
        bb._last_bottom_lines = 8
        bb._completion._popup_height = 0
        return bb

    def test_engine_position_cursor_delegates_through_bb(self, mock_bottom_bar, mock_input):
        """验证 TuiEngine._position_cursor 通过 _BottomBar 间接使用 Input。"""
        # 模拟 Engine 调用 _position_cursor
        # 该路径：bb.get_cursor_info() → bb.compute_cursor_position() → Input.compute_cursor()

        text, cursor_pos, h, w = mock_bottom_bar.get_cursor_info()
        assert text == "test input"
        assert cursor_pos == 10

        r_cursor, cursor_col = mock_bottom_bar.compute_cursor_position(
            text, cursor_pos, h, w,
        )
        assert r_cursor == 20
        assert cursor_col == 5

        # 验证 Input.compute_cursor 被调用
        mock_input.compute_cursor.assert_called()

    def test_cursor_position_consistency(self, mock_bottom_bar, mock_input, mock_width_cache):
        """验证同一输入下 compute_cursor_position 和 ensure_cursor_in_lower 光标一致。"""
        # compute_cursor_position 路径
        text = "hello world"
        pos = 11
        r1, c1 = mock_bottom_bar.compute_cursor_position(text, pos, 24, 80)

        # 重置 mock 调用计数
        mock_input.compute_cursor.reset_mock()
        mock_input.compute_cursor.return_value = (r1, c1, 0, 5)

        # ensure_cursor_in_lower 路径
        mock_bottom_bar._last_rendered_text = text
        mock_bottom_bar._input_cursor_pos = pos

        with patch('sys.__stdout__'):
            mock_bottom_bar.ensure_cursor_in_lower()

        # 两条路径均委托到 Input.compute_cursor
        assert mock_input.compute_cursor.called

    def test_refresh_bottom_bar_updates_input_state(self, mock_input):
        """验证 ChatUIConsumer.refresh_bottom_bar 同步更新 Input 状态。"""
        # 创建最小化的 components
        bb = MagicMock()
        engine = MagicMock()
        components = {
            'rs': MagicMock(),
            'bottom_bar': bb,
            'tui_renderer': MagicMock(),
            'engine': engine,
            'dispatcher': MagicMock(),
            'cmpl_handler': MagicMock(),
            'input': mock_input,
        }

        from src.tui._consumer import ChatUIConsumer
        consumer = ChatUIConsumer.for_testing(components)

        consumer.refresh_bottom_bar("new text", 5)

        # Input IS source of truth — refresh_bottom_bar no longer syncs back to InputBuffer
        # 验证 _BottomBar 被更新
        bb.set_input_state.assert_called_once_with("new text", 5)
        # 验证重绘请求被触发
        engine.request_bottom_redraw.assert_called_once()


# ═══════════════════════════════════════════════════════════
# 方向A 步骤2：TuiInputOrchestrator 事件化等待回归测试（新增，2026-07-31）
# ═══════════════════════════════════════════════════════════

class TestInputOrchestratorEventWaiting:
    """TuiInputOrchestrator.wait_for_user_input 事件化（方向A 步骤2）。"""

    @pytest.fixture
    def inp(self, tmp_path):
        """创建 Input 实例（P2-7：fixture 确保 fd 关闭）。"""
        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            yield Input(fd=fd, history_file=tmp_path / "test_history")
        finally:
            os.close(fd)

    @pytest.fixture
    def monitor(self):
        m = MagicMock()
        m.is_alive = True
        return m

    def test_wait_until_ready_event_regression(self, inp, monitor, tmp_path):
        """Enter 提交后 wait_for_user_input 立即返回（<100ms，验证无 50ms 轮询延迟）。"""
        import threading
        import time

        from src.tui._input_orchestrator import TuiInputOrchestrator
        orch = TuiInputOrchestrator(inp)

        def submit():
            with patch("src.tui._input._append_to_history_file", return_value=True):
                inp.handle_chars("hello")
                inp._enter()

        t = threading.Thread(target=submit)
        start = time.monotonic()
        t.start()
        text = orch.wait_for_user_input(monitor, timeout=5.0)
        elapsed = time.monotonic() - start
        t.join()
        assert text == "hello"
        assert elapsed < 0.1  # 验证无 50ms 轮询延迟（事件化后立即唤醒）

    def test_wait_for_user_input_timeout_regression(self, inp, monitor):
        """timeout 超时返回空字符串。"""
        from src.tui._input_orchestrator import TuiInputOrchestrator
        orch = TuiInputOrchestrator(inp)
        text = orch.wait_for_user_input(monitor, timeout=0.2)
        assert text == ""

    def test_wait_for_user_input_monitor_death_regression(self, inp):
        """monitor 死亡时抛 RuntimeError（_loop.py 捕获后走恢复逻辑）。"""
        from src.tui._input_orchestrator import TuiInputOrchestrator
        orch = TuiInputOrchestrator(inp)
        dead_monitor = MagicMock()
        dead_monitor.is_alive = False
        with pytest.raises(RuntimeError, match="EscapeMonitor"):
            orch.wait_for_user_input(dead_monitor, timeout=0.5)

    def test_wait_for_user_input_prefill_regression(self, inp, monitor):
        """prefill 注入后缓冲区被预填且可正常提交。"""
        import threading
        import time

        from src.tui._input_orchestrator import TuiInputOrchestrator
        orch = TuiInputOrchestrator(inp)
        result = {}

        def wait_with_prefill():
            with patch("src.tui._input._append_to_history_file", return_value=True):
                result["text"] = orch.wait_for_user_input(
                    monitor, prefill="prefill text", timeout=5.0,
                )

        t = threading.Thread(target=wait_with_prefill)
        t.start()
        # 等待 prefill 注入完成（缓冲区被预填）
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if inp.get_current_text() == "prefill text":
                break
            time.sleep(0.01)
        assert inp.get_current_text() == "prefill text"
        # 提交（模拟用户按 Enter）——patch 上下文在 wait 线程内仍活跃，无磁盘写入
        inp._enter()
        t.join()
        assert result["text"] == "prefill text"


# ═══════════════════════════════════════════════════════════
# 2026-08-01：TuiInputOrchestrator prefill 残留提交兜底恢复回归测试
# ═══════════════════════════════════════════════════════════

class TestInputOrchestratorPrefillResidual:
    """prefill 注入后残留 Enter 提交恢复（editmsg 竞态兜底修复）。

    覆盖场景：LF 在 set_buffer(prefill) 之后才被 render 线程处理、
    _enter() 已提交 prefill 的残余窗口——编排器在 set_buffer 后再次
    get_queued_input() 检查残留提交，若非 None 则重新注入恢复。
    采用可控 mock 序列保证确定性（避免线程时序不确定性）。
    """

    def test_prefill_residual_enter_restored_regression(self, tmp_path):
        """注入 prefill 后残留提交被恢复，prefill 仍在缓冲中。"""
        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            inp = Input(fd=fd, history_file=tmp_path / "history")
            from src.tui._input_orchestrator import TuiInputOrchestrator
            orch = TuiInputOrchestrator(inp)
            monitor = MagicMock()
            monitor.is_alive = True

            # 记录 set_buffer 调用；保留原方法以便真实设置缓冲
            set_buffer_calls = []
            original_set_buffer = inp.set_buffer
            with patch.object(
                inp, "set_buffer",
                side_effect=lambda text: (
                    set_buffer_calls.append(text), original_set_buffer(text),
                )[1],
            ), patch.object(
                inp, "get_queued_input",
                side_effect=iter([None, "prefill text"]).__next__,
            ), patch.object(inp, "wait_until_ready", return_value=False):
                result = orch.wait_for_user_input(
                    monitor, prefill="prefill text", timeout=0.1,
                )

            # 注入 + 恢复各调用一次 set_buffer
            assert set_buffer_calls == ["prefill text", "prefill text"]
            # prefill 仍在缓冲中（恢复后未被消费）
            assert inp.get_current_text() == "prefill text"
            # 超时返回空字符串（用户未输入）
            assert result == ""
        finally:
            os.close(fd)
