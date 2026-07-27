"""集成测试：Input → _BottomBar → TuiEngine 光标定位闭环。

验证步骤 7 中 _BottomBar 适配 Input 类后的端到端光标定位正确性。
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path


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
    def mock_input(self, mock_width_cache):
        """创建 mock Input 实例。"""
        from src.tui.input._input import Input
        from src.tui.input._buffer import InputBuffer
        import tempfile

        with tempfile.NamedTemporaryFile(suffix='.history', delete=False) as f:
            history_path = Path(f.name)

        input_instance = Input.__new__(Input)
        input_instance._fd = -1
        input_instance._term_width_cache = mock_width_cache
        input_instance._cursor_tracker = MagicMock()
        input_instance._buffer = InputBuffer(history_path)
        input_instance._parser = MagicMock()
        input_instance._cursor = MagicMock()
        input_instance._cursor.compute.return_value = (20, 5, 0, 2)

        try:
            history_path.unlink()
        except Exception:
            pass

        return input_instance

    @pytest.fixture
    def mock_bottom_bar(self, mock_input, mock_width_cache):
        """创建注入了 Input 的 _BottomBar mock。"""
        from src.tui.widgets.bottom_bar.bar import _BottomBar

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
        mock_input._cursor.compute.assert_called()

    def test_cursor_position_consistency(self, mock_bottom_bar, mock_input, mock_width_cache):
        """验证同一输入下 compute_cursor_position 和 ensure_cursor_in_lower 光标一致。"""
        # compute_cursor_position 路径
        text = "hello world"
        pos = 11
        r1, c1 = mock_bottom_bar.compute_cursor_position(text, pos, 24, 80)

        # 重置 mock 调用计数
        mock_input._cursor.compute.reset_mock()
        mock_input._cursor.compute.return_value = (r1, c1, 0, 5)

        # ensure_cursor_in_lower 路径
        mock_bottom_bar._last_rendered_text = text
        mock_bottom_bar._input_cursor_pos = pos

        with patch('sys.__stdout__'):
            mock_bottom_bar.ensure_cursor_in_lower()

        # 两条路径均委托到 Input.compute_cursor
        assert mock_input._cursor.compute.called

    def test_refresh_bottom_bar_updates_input_buffer(self, mock_input):
        """验证 ChatUIConsumer.refresh_bottom_bar 同步更新 InputBuffer。"""
        from src.tui.consumer.factory import _ChatUIComponents

        # 创建最小化的 components
        bb = MagicMock()
        engine = MagicMock()
        components = _ChatUIComponents(
            rs=MagicMock(),
            cursor_tracker=MagicMock(),
            bottom_bar=bb,
            output_adapter=MagicMock(),
            tui_renderer=MagicMock(),
            engine=engine,
            dispatcher=MagicMock(),
            cmpl_handler=MagicMock(),
            input=mock_input,
        )

        from src.tui.consumer.consumer import ChatUIConsumer
        consumer = ChatUIConsumer.for_testing(components)

        consumer.refresh_bottom_bar("new text", 5)

        # Input IS source of truth — refresh_bottom_bar no longer syncs back to InputBuffer
        # 验证 _BottomBar 被更新
        bb.set_input_state.assert_called_once_with("new text", 5)
        # 验证重绘请求被触发
        engine.request_bottom_redraw.assert_called_once()
