"""chat_ui 光标定位模块单元测试 — CursorController。

测试覆盖：
  - _write_ansi(): blessed 成功路径、blessed 失败 fallback 路径
  - position_cursor(): 正常路径、_get_terminal 异常路径
  - ensure_cursor_upper(): 委托 _bb.ensure_cursor_in_upper
  - move_cursor_to_bottom(): 正常路径、_get_terminal 异常路径、ANSI fallback
  - 构造注入: get_terminal=None 使用模块级默认值
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, call, patch

import pytest

pytestmark = pytest.mark.skip("光标逻辑已迁移至 _engine._position_cursor()，测试在 test_chat_ui_engine.py 中覆盖")

# ── 将项目根目录加入 sys.path（Termux 环境需要）───
sys.path.insert(0, "/home/DeepSeek-cli")


# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════

@pytest.fixture
def mock_bb():
    """Mock BottomBarProtocol。"""
    bb = MagicMock()
    bb.get_cursor_info.return_value = ("hello", 3, 1, 80)
    bb.compute_cursor_position.return_value = (1, 4)
    return bb


@pytest.fixture
def mock_term():
    """Mock blessed terminal。"""
    term = MagicMock()
    term.move_xy.return_value = "\033[1;4H"
    term.height = 24
    return term


@pytest.fixture
def cursor(mock_bb, mock_term):
    """CursorController 实例，注入 mock get_terminal。"""
    from src.chat_ui._cursor import CursorController
    return CursorController(mock_bb, get_terminal=lambda: mock_term)


@pytest.fixture
def cursor_default_import(mock_bb):
    """CursorController 实例，使用默认模块级 import（get_terminal=None）。"""
    from src.chat_ui._cursor import CursorController
    return CursorController(mock_bb, get_terminal=None)


# ═══════════════════════════════════════════════════════════
# Test _write_ansi
# ═══════════════════════════════════════════════════════════

class TestWriteAnsi:
    """_write_ansi 私有方法测试。"""

    def test_write_ansi_blessed_success(self, cursor):
        """blessed get_terminal 成功 → 写 blessed 转义 + flush。"""
        text = "\033[1;4H"
        with (
            patch.object(cursor, "_get_terminal") as mock_get,
            patch("sys.__stdout__") as mock_stdout,
        ):
            mock_get.return_value.move_xy.return_value = text
            cursor._write_ansi(text, "\033[99;99H")

        mock_stdout.write.assert_called_once_with(text)
        mock_stdout.flush.assert_called_once()

    def test_write_ansi_blessed_fallback(self, cursor):
        """blessed get_terminal 抛出异常 → 回退写 fallback ANSI + flush。"""
        fallback = "\033[99;99H"
        with (
            patch.object(cursor, "_get_terminal", side_effect=RuntimeError("term lost")),
            patch("sys.__stdout__") as mock_stdout,
        ):
            cursor._write_ansi("\033[1;4H", fallback)

        mock_stdout.write.assert_called_once_with(fallback)
        mock_stdout.flush.assert_called_once()

    def test_write_ansi_stdout_write_fallback(self, cursor):
        """sys.__stdout__.write 抛出异常 → 回退写 fallback ANSI + flush。"""
        fallback = "\033[99;99H"
        with (
            patch.object(cursor, "_get_terminal") as mock_get,
            patch("sys.__stdout__") as mock_stdout,
        ):
            mock_term = MagicMock()
            mock_get.return_value = mock_term
            # side_effect 为列表：[第1次调用抛出, 第2次返回None]
            mock_stdout.write.side_effect = [OSError("stdout closed"), None]
            cursor._write_ansi("\033[1;4H", fallback)

        # except 分支捕获后调用了 fallback 写入
        mock_stdout.write.assert_has_calls([
            call("\033[1;4H"),    # 第一次：text → 抛异常
            call(fallback),        # 第二次：fallback → 成功
        ])
        # flush 应该被精确调用一次（在 except 块外部）
        assert mock_stdout.flush.call_count == 1


# ═══════════════════════════════════════════════════════════
# Test position_cursor
# ═══════════════════════════════════════════════════════════

class TestPositionCursor:
    """position_cursor 方法测试。"""

    def test_position_cursor_normal(self, cursor, mock_bb, mock_term):
        """正常路径：_get_terminal 成功 → move_xy 定位 + flush。"""
        with patch("sys.__stdout__") as mock_stdout:
            cursor.position_cursor()

        # 验证调用了 _bb 的公开 API
        mock_bb.get_cursor_info.assert_called_once()
        mock_bb.compute_cursor_position.assert_called_once()
        # 验证 write + flush
        mock_stdout.write.assert_called_once()
        mock_stdout.flush.assert_called_once()

    def test_position_cursor_get_terminal_fallback(self, cursor, mock_bb):
        """_get_terminal 抛出异常 → 回退 ANSI 定位 + flush。"""
        with (
            patch.object(cursor, "_get_terminal", side_effect=RuntimeError("term lost")),
            patch("sys.__stdout__") as mock_stdout,
        ):
            cursor.position_cursor()

        # 验证写入了 ANSI fallback（\033[1;4H = r_cursor=1, cursor_col=4）
        mock_stdout.write.assert_called_once_with("\033[1;4H")
        mock_stdout.flush.assert_called_once()

    def test_position_cursor_calls_bb_public_api(self, cursor, mock_bb):
        """验证委托了 _bb.get_cursor_info() + compute_cursor_position()。"""
        with (
            patch.object(cursor, "_get_terminal") as mock_get,
            patch("sys.__stdout__"),
        ):
            mock_get.return_value.move_xy.return_value = "\033[1;4H"
            cursor.position_cursor()

        mock_bb.get_cursor_info.assert_called_once()
        mock_bb.compute_cursor_position.assert_called_once_with(
            mock_bb.get_cursor_info.return_value[0],
            mock_bb.get_cursor_info.return_value[1],
            mock_bb.get_cursor_info.return_value[2],
            mock_bb.get_cursor_info.return_value[3],
        )


# ═══════════════════════════════════════════════════════════
# Test ensure_cursor_upper
# ═══════════════════════════════════════════════════════════

class TestEnsureCursorUpper:
    """ensure_cursor_upper 方法测试。"""

    def test_ensure_cursor_upper_delegates(self, cursor, mock_bb):
        """验证委托了 _bb.ensure_cursor_in_upper()。"""
        cursor.ensure_cursor_upper()
        mock_bb.ensure_cursor_in_upper.assert_called_once()


# ═══════════════════════════════════════════════════════════
# Test move_cursor_to_bottom
# ═══════════════════════════════════════════════════════════

class TestMoveCursorToBottom:
    """move_cursor_to_bottom 方法测试。"""

    def test_move_cursor_to_bottom_normal(self, cursor, mock_term):
        """正常路径：_get_terminal 成功 → term.move_xy() + flush。"""
        with (
            patch.object(cursor, "_get_terminal", return_value=mock_term),
            patch("sys.__stdout__") as mock_stdout,
        ):
            cursor.move_cursor_to_bottom()

        mock_term.move_xy.assert_called_once_with(0, mock_term.height - 1)
        mock_stdout.write.assert_called_once()
        mock_stdout.flush.assert_called_once()

    def test_move_cursor_to_bottom_get_terminal_fallback(self, cursor):
        """_get_terminal 抛出异常 → 回退 _ANSI_CURSOR_BOTTOM + flush。"""
        with (
            patch.object(cursor, "_get_terminal", side_effect=RuntimeError("term lost")),
            patch("sys.__stdout__") as mock_stdout,
        ):
            cursor.move_cursor_to_bottom()

        from src.chat_ui.const import _ANSI_CURSOR_BOTTOM
        mock_stdout.write.assert_called_once_with(_ANSI_CURSOR_BOTTOM)
        mock_stdout.flush.assert_called_once()

    def test_move_cursor_to_bottom_stdout_fallback(self, cursor, mock_term):
        """move_cursor_to_bottom 中 _write_ansi 内 write 失败 → fallback。"""
        with (
            patch.object(cursor, "_get_terminal", return_value=mock_term),
            patch("sys.__stdout__") as mock_stdout,
        ):
            # side_effect 为列表：[第1次调用抛出, 第2次返回None]
            mock_stdout.write.side_effect = [OSError("stdout closed"), None]

            from src.chat_ui.const import _ANSI_CURSOR_BOTTOM
            cursor.move_cursor_to_bottom()

        # _write_ansi 捕获异常后写 fallback（带 blessed move_xy 的 ANSI）
        ans_seq = mock_term.move_xy.return_value
        mock_stdout.write.assert_has_calls([
            call(ans_seq),      # 第一次：text → 抛异常
            call(_ANSI_CURSOR_BOTTOM),  # 第二次：fallback → 成功
        ])
        assert mock_stdout.flush.call_count == 1


# ═══════════════════════════════════════════════════════════
# Test constructor
# ═══════════════════════════════════════════════════════════

class TestConstructor:
    """CursorController 构造测试。"""

    def test_get_terminal_default_import(self, cursor_default_import, mock_bb):
        """get_terminal=None 时使用模块级 _get_terminal 默认值。"""
        # 验证构造成功，_get_terminal 是 callable
        assert callable(cursor_default_import._get_terminal)

    def test_get_terminal_injected(self, cursor):
        """注入 get_terminal 回调正常工作。"""
        # 验证构造成功，_get_terminal 是注入的 lambda
        assert callable(cursor._get_terminal)
