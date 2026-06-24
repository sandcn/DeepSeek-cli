"""run_bottom_bar_selection KEY_ENTER 处理测试

验证 run_bottom_bar_selection 在收到 KEY_ENTER 序列键
和普通 Enter 字符时均能正确确认选择。

测试策略：
  - Mock Blessed Terminal.inkey() 返回模拟 Keystroke 对象
  - Mock src.chat_ui.get_active_chat_ui、_BottomBar、sys.stdin、os.isatty
  - 验证 KEY_ENTER(343)、'\\r'、'\\n' 三种 Enter 形式均返回 confirmed
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from src.ui._bottom_bar_selection import run_bottom_bar_selection, _KEY_ENTER, _KEY_UP, _KEY_DOWN, _KEY_ESCAPE

# 统一的 patch 目标
_TERMINAL_PATCH = "src.ui._bottom_bar_selection.get_terminal"


class _MockKeystroke:
    """模拟 Blessed Keystroke 对象。"""

    def __init__(self, key=None, is_sequence=False, code=None, name=""):
        self._key = key
        self._is_sequence = is_sequence
        self._code = code
        self.name = name

    def __eq__(self, other):
        if isinstance(other, _MockKeystroke):
            return self._key == other._key
        return self._key == other

    def __hash__(self):
        return hash(self._key)

    def __repr__(self):
        return f"_MockKeystroke({self._key!r})"

    @property
    def is_sequence(self):
        return self._is_sequence

    @property
    def code(self):
        return self._code

    def __str__(self):
        return str(self._key) if self._key else ""


class TestRunBottomBarSelectionEnter(unittest.TestCase):
    """验证 run_bottom_bar_selection 对各类 Enter 按键的处理。"""

    def setUp(self):
        self._stdout = sys.__stdout__

    def tearDown(self):
        sys.__stdout__ = self._stdout

    def _make_mock_chat_ui(self):
        """创建模拟的 ChatUI，包含活跃的 _BottomBar。"""
        mock_bb = MagicMock()
        mock_bb._active = True
        mock_bb._completion_idx = 0  # 默认选中第一条
        mock_bb.show_completions.return_value = None

        mock_chat_ui = MagicMock()
        mock_chat_ui._bottom_bar = mock_bb
        return mock_chat_ui

    def _make_mock_terminal(self, keys):
        """创建模拟 Blessed Terminal，按顺序返回 key 列表。"""
        mock_term = MagicMock()
        mock_term.__enter__ = MagicMock(return_value=mock_term)
        mock_term.__exit__ = MagicMock(return_value=False)
        mock_term.inkey.side_effect = keys
        return mock_term

    def _run_with_mocks(self, mock_chat_ui, mock_term, items, display_items,
                        initial_idx=0, title="测试"):
        """在完整 mock 环境下运行 run_bottom_bar_selection。"""
        mock_stdin = MagicMock()
        mock_stdin.fileno.return_value = 0

        with patch(_TERMINAL_PATCH, return_value=mock_term), \
             patch("sys.stdin", mock_stdin), \
             patch("os.isatty", return_value=True), \
             patch.object(sys, '__stdout__', MagicMock()):
            return run_bottom_bar_selection(
                items=items,
                display_items=display_items,
                initial_idx=initial_idx,
                title=title,
                bottom_bar=mock_chat_ui._bottom_bar,
            )

    # ── KEY_ENTER 序列键确认 ─────────────────────────

    def test_sequence_key_enter_confirms_selection(self):
        """KEY_ENTER(343) 序列键应确认选择。"""
        mock_chat_ui = self._make_mock_chat_ui()
        enter_key = _MockKeystroke(is_sequence=True, code=_KEY_ENTER)
        mock_term = self._make_mock_terminal([enter_key])

        result = self._run_with_mocks(
            mock_chat_ui, mock_term,
            items=["item_a", "item_b", "item_c"],
            display_items=["A", "B", "C"],
        )

        self.assertEqual(result["action"], "confirmed")
        self.assertEqual(result["index"], 0)

    def test_sequence_key_enter_with_nonzero_index(self):
        """KEY_ENTER 应在 _completion_idx 非 0 时正确返回索引。"""
        mock_chat_ui = self._make_mock_chat_ui()
        mock_chat_ui._bottom_bar._completion_idx = 2
        enter_key = _MockKeystroke(is_sequence=True, code=_KEY_ENTER)
        mock_term = self._make_mock_terminal([enter_key])

        result = self._run_with_mocks(
            mock_chat_ui, mock_term,
            items=["item_a", "item_b", "item_c"],
            display_items=["A", "B", "C"],
            initial_idx=2,
        )

        self.assertEqual(result["action"], "confirmed")
        self.assertEqual(result["index"], 2)

    # ── 非序列 Enter（'\\r', '\\n'）回归测试 ─────────

    def test_carriage_return_confirms_selection(self):
        """\\r 字符应确认选择（回归测试）。"""
        mock_chat_ui = self._make_mock_chat_ui()
        enter_key = _MockKeystroke(key='\r', is_sequence=False)
        mock_term = self._make_mock_terminal([enter_key])

        result = self._run_with_mocks(
            mock_chat_ui, mock_term,
            items=["item_a", "item_b", "item_c"],
            display_items=["A", "B", "C"],
        )

        self.assertEqual(result["action"], "confirmed")
        self.assertEqual(result["index"], 0)

    def test_newline_confirms_selection(self):
        """\\n 字符应确认选择（回归测试）。"""
        mock_chat_ui = self._make_mock_chat_ui()
        enter_key = _MockKeystroke(key='\n', is_sequence=False)
        mock_term = self._make_mock_terminal([enter_key])

        result = self._run_with_mocks(
            mock_chat_ui, mock_term,
            items=["item_a", "item_b", "item_c"],
            display_items=["A", "B", "C"],
        )

        self.assertEqual(result["action"], "confirmed")
        self.assertEqual(result["index"], 0)

    # ── KEY_ESCAPE 取消回归测试 ──────────────────────

    def test_sequence_escape_cancels(self):
        """KEY_ESCAPE(361) 序列键应取消选择。"""
        mock_chat_ui = self._make_mock_chat_ui()
        esc_key = _MockKeystroke(is_sequence=True, code=_KEY_ESCAPE)
        mock_term = self._make_mock_terminal([esc_key])

        result = self._run_with_mocks(
            mock_chat_ui, mock_term,
            items=["item_a", "item_b", "item_c"],
            display_items=["A", "B", "C"],
        )

        self.assertEqual(result["action"], "cancel")
        self.assertIsNone(result["index"])

    # ── _completion_idx 验证 ─────────────────────────

    def test_sequence_key_enter_respects_completion_idx(self):
        """KEY_ENTER 在 _completion_idx 为 1 时返回索引 1。"""
        mock_chat_ui = self._make_mock_chat_ui()
        mock_chat_ui._bottom_bar._completion_idx = 1
        enter_key = _MockKeystroke(is_sequence=True, code=_KEY_ENTER)
        mock_term = self._make_mock_terminal([enter_key])

        result = self._run_with_mocks(
            mock_chat_ui, mock_term,
            items=["a", "b", "c"],
            display_items=["A", "B", "C"],
        )

        self.assertEqual(result["action"], "confirmed")
        self.assertEqual(result["index"], 1)

    # ── ↑↓ 导航回归测试 ─────────────────────────────

    def test_arrow_up_cycles_completion(self):
        """↑ 键应调用 cycle_completion(-1)。"""
        mock_chat_ui = self._make_mock_chat_ui()
        up_key = _MockKeystroke(is_sequence=True, code=_KEY_UP)
        enter_key = _MockKeystroke(is_sequence=True, code=_KEY_ENTER)
        mock_term = self._make_mock_terminal([up_key, enter_key])

        self._run_with_mocks(
            mock_chat_ui, mock_term,
            items=["a", "b", "c"],
            display_items=["A", "B", "C"],
        )

        mock_chat_ui._bottom_bar.cycle_completion.assert_called_with(-1)

    def test_arrow_down_cycles_completion(self):
        """↓ 键应调用 cycle_completion(1)。"""
        mock_chat_ui = self._make_mock_chat_ui()
        down_key = _MockKeystroke(is_sequence=True, code=_KEY_DOWN)
        enter_key = _MockKeystroke(is_sequence=True, code=_KEY_ENTER)
        mock_term = self._make_mock_terminal([down_key, enter_key])

        self._run_with_mocks(
            mock_chat_ui, mock_term,
            items=["a", "b", "c"],
            display_items=["A", "B", "C"],
        )

        mock_chat_ui._bottom_bar.cycle_completion.assert_called_with(1)

    # ── 其他序列键忽略 ───────────────────────────────

    def test_unknown_sequence_ignored(self):
        """未知序列键（如 F1=265）应被忽略，循环继续直到 Enter。"""
        mock_chat_ui = self._make_mock_chat_ui()
        unknown_key = _MockKeystroke(is_sequence=True, code=265)
        enter_key = _MockKeystroke(key='\r', is_sequence=False)
        mock_term = self._make_mock_terminal([unknown_key, enter_key])

        result = self._run_with_mocks(
            mock_chat_ui, mock_term,
            items=["a", "b"],
            display_items=["A", "B"],
        )

        self.assertEqual(result["action"], "confirmed")
        self.assertEqual(result["index"], 0)

    # ── '\\x1b' 取消 ─────────────────────────────────

    def test_raw_escape_cancels(self):
        """'\\x1b' 字符应取消选择。"""
        mock_chat_ui = self._make_mock_chat_ui()
        esc_key = _MockKeystroke(key='\x1b', is_sequence=False)
        mock_term = self._make_mock_terminal([esc_key])

        result = self._run_with_mocks(
            mock_chat_ui, mock_term,
            items=["a", "b"],
            display_items=["A", "B"],
        )

        self.assertEqual(result["action"], "cancel")
        self.assertIsNone(result["index"])


if __name__ == "__main__":
    unittest.main()
