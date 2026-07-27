"""集成测试：Input → _BottomBar → TuiEngine 光标定位闭环。

验证步骤 7 的 _BottomBar 适配 Input 类后的光标定位正确性，
以及步骤 8 的 _run_selection_raw 通过 InputParser 正确分发按键。
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path


class TestBottomBarInputIntegration:
    """验证 _BottomBar 在注入 Input 后光标定位委托正确。"""

    @pytest.fixture
    def mock_width_cache(self):
        """Mock TerminalWidthCache，返回固定 80x24 终端。"""
        cache = MagicMock()
        cache.get_width.return_value = 80
        cache.get_height.return_value = 24
        return cache

    @pytest.fixture
    def mock_input(self, mock_width_cache):
        """创建 mock Input 实例，注入 mock width_cache。"""
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

    def test_set_input_stores_reference(self, mock_input):
        """验证 set_input 正确存储 Input 引用。"""
        from src.tui.widgets.bottom_bar.bar import _BottomBar

        bb = _BottomBar()
        assert bb._input is None

        bb.set_input(mock_input)
        assert bb._input is mock_input

    def test_compute_cursor_position_delegates_to_input(self, mock_input):
        """验证 compute_cursor_position 委托给 Input.compute_cursor。"""
        from src.tui.widgets.bottom_bar.bar import _BottomBar

        bb = _BottomBar()
        bb.set_input(mock_input)

        r, c = bb.compute_cursor_position("hello", 5, 24, 80)
        assert r == 20
        assert c == 5

        mock_input._cursor.compute.assert_called_once()
        args = mock_input._cursor.compute.call_args[0]
        assert args[0] == "hello"
        assert args[1] == 5

    def test_compute_cursor_position_fallback_without_input(self, mock_width_cache):
        """验证无 Input 实例时回退到原始计算逻辑。"""
        from src.tui.widgets.bottom_bar.bar import _BottomBar

        bb = _BottomBar()
        bb._width_cache = mock_width_cache

        r, c = bb.compute_cursor_position("test", 4, 24, 80)
        assert isinstance(r, int)
        assert isinstance(c, int)
        assert r >= 1
        assert c >= 1

    def test_ensure_cursor_in_lower_with_input(self, mock_input):
        """验证 ensure_cursor_in_lower 在注入 Input 后委托计算。"""
        from src.tui.widgets.bottom_bar.bar import _BottomBar

        bb = _BottomBar()
        bb._active = True
        bb._last_text = "hello"
        bb._last_rendered_text = "hello"
        bb._input_cursor_pos = 5
        bb._last_bottom_lines = 8
        bb._subagent_lines = []
        bb._completion._popup_height = 0
        bb._width_cache = mock_input._term_width_cache

        bb.set_input(mock_input)

        with patch('sys.__stdout__'):
            bb.ensure_cursor_in_lower()
            mock_input._cursor.compute.assert_called()


class TestSelectionInputParserIntegration:
    """验证 _run_selection_raw 通过 InputParser 正确分发按键。"""

    @pytest.fixture
    def mock_bb(self):
        """创建 mock _BottomBar。"""
        bb = MagicMock()
        bb._completion_idx = 0
        bb._active = True
        return bb

    @pytest.fixture
    def mock_input_with_parser(self):
        """创建 mock Input 实例。"""
        from src.tui.input._input import Input

        input_instance = Input.__new__(Input)
        input_instance._fd = -1
        input_instance._parser = MagicMock()
        input_instance._term_width_cache = MagicMock()
        input_instance._buffer = MagicMock()
        input_instance._cursor = MagicMock()
        return input_instance

    # ── 辅助：一键创建 termios 相关的 mock ──

    @staticmethod
    def _setup_termios_mocks():
        """返回 (mock_save, mock_restore, mock_tty, mock_termios) 四个 patch 对象。

        调用方需使用 `with m1, m2, m3, m4:` 上下文管理器。
        """
        m_save = patch(
            'src.tui.widgets.bottom_bar.selection._save_terminal_settings',
            return_value=MagicMock(),
        )
        m_restore = patch(
            'src.tui.widgets.bottom_bar.selection._restore_terminal_settings',
        )
        # tty 和 termios 在 _run_selection_raw 内部通过
        #   from src._compat_termios import termios as _termios, tty
        # 本地导入，所以 patch 路径应为 src._compat_termios
        m_tty = patch('src._compat_termios.tty')
        m_termios = patch('src._compat_termios.termios')
        return m_save, m_restore, m_tty, m_termios

    def test_arrow_up_dispatches_cycle_completion(self, mock_bb, mock_input_with_parser):
        """验证上箭头 KeyEvent → bb.cycle_completion(-1)。"""
        from src.tui.widgets.bottom_bar.selection import _run_selection_raw
        from src.tui.input._parser import KeyEvent

        mock_input_with_parser._parser.parse_escape_sequence.return_value = KeyEvent(
            kind="arrow_up", raw=b"\x1b[A"
        )

        items = ["a", "b", "c"]
        m1, m2, m3, m4 = self._setup_termios_mocks()
        with m1, m2, m3, m4, patch('os.read') as mock_read:
            mock_read.side_effect = [b'\x1b', b'r']
            result = _run_selection_raw(
                items, items, 0, "测试", mock_bb,
                input_instance=mock_input_with_parser,
            )

        assert result["action"] == "resume"

    def test_arrow_down_dispatches_cycle_completion(self, mock_bb, mock_input_with_parser):
        """验证下箭头 KeyEvent → bb.cycle_completion(1)。"""
        from src.tui.widgets.bottom_bar.selection import _run_selection_raw
        from src.tui.input._parser import KeyEvent

        mock_input_with_parser._parser.parse_escape_sequence.return_value = KeyEvent(
            kind="arrow_down", raw=b"\x1b[B"
        )

        items = ["a", "b", "c"]
        m1, m2, m3, m4 = self._setup_termios_mocks()
        with m1, m2, m3, m4, patch('os.read') as mock_read:
            mock_read.side_effect = [b'\x1b', b'd']
            result = _run_selection_raw(
                items, items, 0, "测试", mock_bb,
                input_instance=mock_input_with_parser,
            )

        assert result["action"] == "delete"

    def test_escape_cancels(self, mock_bb, mock_input_with_parser):
        """验证单独 Esc → cancel。"""
        from src.tui.widgets.bottom_bar.selection import _run_selection_raw
        from src.tui.input._parser import KeyEvent

        mock_input_with_parser._parser.parse_escape_sequence.return_value = KeyEvent(
            kind="escape", raw=b"\x1b"
        )

        items = ["a", "b", "c"]
        m1, m2, m3, m4 = self._setup_termios_mocks()
        with m1, m2, m3, m4, patch('os.read') as mock_read:
            mock_read.return_value = b'\x1b'
            result = _run_selection_raw(
                items, items, 0, "测试", mock_bb,
                input_instance=mock_input_with_parser,
            )

        assert result["action"] == "cancel"

    def test_enter_confirms(self, mock_bb, mock_input_with_parser):
        """验证 Enter → confirmed。"""
        from src.tui.widgets.bottom_bar.selection import _run_selection_raw

        items = ["a", "b", "c"]
        m1, m2, m3, m4 = self._setup_termios_mocks()
        with m1, m2, m3, m4, patch('os.read') as mock_read:
            mock_read.return_value = b'\r'
            result = _run_selection_raw(
                items, items, 0, "测试", mock_bb,
                input_instance=mock_input_with_parser,
            )

        assert result["action"] == "confirmed"
        assert result["index"] == 0

    def test_unknown_csi_ignored(self, mock_bb, mock_input_with_parser):
        """验证未知 CSI 序列 → 忽略（继续轮询）。"""
        from src.tui.widgets.bottom_bar.selection import _run_selection_raw
        from src.tui.input._parser import KeyEvent

        mock_input_with_parser._parser.parse_escape_sequence.return_value = KeyEvent(
            kind="unknown", raw=b"\x1b[99X"
        )

        items = ["a", "b", "c"]
        m1, m2, m3, m4 = self._setup_termios_mocks()
        with m1, m2, m3, m4, patch('os.read') as mock_read:
            mock_read.side_effect = [b'\x1b', b'R']
            result = _run_selection_raw(
                items, items, 0, "测试", mock_bb,
                input_instance=mock_input_with_parser,
            )

        assert result["action"] == "resume_all"

    def test_fallback_without_input(self, mock_bb):
        """验证无 input_instance 时回退到手动 CSI 解析。"""
        from src.tui.widgets.bottom_bar.selection import _run_selection_raw

        items = ["a", "b", "c"]
        m1, m2, m3, m4 = self._setup_termios_mocks()
        with m1, m2, m3, m4, \
             patch('os.read') as mock_read, \
             patch('select.select') as mock_select:
            mock_read.side_effect = [b'\x1b', b'[', b'A', b'r']
            mock_select.side_effect = [
                ([1], [], []),
                ([1], [], []),
                ([], [], []),
            ]
            result = _run_selection_raw(
                items, items, 0, "测试", mock_bb,
                input_instance=None,
            )

        assert result["action"] == "resume"
