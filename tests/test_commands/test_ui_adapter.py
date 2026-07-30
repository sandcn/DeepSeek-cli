"""测试 _ui_adapter.py — CommandUiAdapter 底部栏选择交互。

测试 run_bottom_bar_selection 方法的确认/取消/异常三种场景。
使用 unittest.mock 模拟 _BottomBar，不执行真实终端 I/O。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.core.commands._ui_adapter import CommandUiAdapter


class TestRunBottomBarSelection:
    """测试 run_bottom_bar_selection 方法。"""

    def _make_mock_bottom_bar(self) -> MagicMock:
        bb = MagicMock()
        bb._input = MagicMock()
        bb._input.get_queued_input.return_value = None
        bb.get_selected_completion_index.return_value = 0
        bb.get_selected_completion.return_value = ("", 0, "")
        return bb

    # ── 场景 1：用户选中索引 2 后确认 ──

    @patch("time.sleep")
    @patch("time.monotonic")
    @patch.dict("sys.modules", {"src.core.api.escape_monitor._monitor": MagicMock()})
    def test_run_bottom_bar_selection_confirmed(
        self, mock_monotonic: MagicMock, mock_sleep: MagicMock,
    ):
        """用户选中索引 2 后确认 → 返回 {"action": "confirmed", "index": 2}。"""
        items = ["item_a", "item_b", "item_c"]
        display_items = ["A", "B", "C"]

        bb = self._make_mock_bottom_bar()
        bb._input.get_queued_input.return_value = "\n"  # 用户按下 Enter
        bb.get_selected_completion_index.return_value = 2

        # time.monotonic: 100 → deadline=160, 120 < 160 → 进入循环
        mock_monotonic.side_effect = [100.0, 120.0]

        adapter = CommandUiAdapter()
        result = adapter.run_bottom_bar_selection(
            items, display_items, initial_idx=0, title="选择", bottom_bar=bb,
        )

        assert result == {"action": "confirmed", "index": 2}
        bb.show_completions.assert_called_once()
        bb.get_selected_completion_index.assert_called_once()
        bb.hide_completions.assert_called_once()

    # ── 场景 2：超时取消 ──

    @patch("time.sleep")
    @patch("time.monotonic")
    def test_run_bottom_bar_selection_cancel(
        self, mock_monotonic: MagicMock, mock_sleep: MagicMock,
    ):
        """超时未选择 → 返回 {"action": "cancel", "index": None}。"""
        items = ["item_a", "item_b"]
        display_items = ["A", "B"]

        bb = self._make_mock_bottom_bar()

        # time.monotonic: 100 → deadline=160, 170 > 160 → 循环条件立即失败
        mock_monotonic.side_effect = [100.0, 170.0]

        adapter = CommandUiAdapter()
        result = adapter.run_bottom_bar_selection(
            items, display_items, initial_idx=0, title="选择", bottom_bar=bb,
        )

        assert result == {"action": "cancel", "index": None}
        bb.show_completions.assert_called_once()
        # 未进入循环，不应调用以下方法
        bb.get_selected_completion.assert_not_called()
        bb.get_selected_completion_index.assert_not_called()
        bb.hide_completions.assert_called_once()  # 超时后隐藏弹窗

    # ── 场景 3：show_completions 异常 → error ──

    @patch("time.sleep")
    @patch("time.monotonic")
    def test_run_bottom_bar_selection_error(
        self, mock_monotonic: MagicMock, mock_sleep: MagicMock,
    ):
        """show_completions 抛出异常 → 返回 {"action": "error", "index": None}。"""
        items = ["item_a", "item_b"]
        display_items = ["A", "B"]

        bb = self._make_mock_bottom_bar()
        bb.show_completions.side_effect = RuntimeError("test error")

        adapter = CommandUiAdapter()
        result = adapter.run_bottom_bar_selection(
            items, display_items, initial_idx=0, title="选择", bottom_bar=bb,
        )

        assert result == {"action": "error", "index": None}
        bb.show_completions.assert_called_once()
        bb.get_selected_completion.assert_not_called()
        bb.get_selected_completion_index.assert_not_called()
        bb.hide_completions.assert_not_called()  # 弹窗未显示，无需隐藏

    # ── 场景 4：bottom_bar 为 None → error ──

    def test_run_bottom_bar_selection_no_bottom_bar(self):
        """bottom_bar 为 None → 返回 {"action": "error", "index": None}。"""
        adapter = CommandUiAdapter()
        result = adapter.run_bottom_bar_selection(
            ["a", "b"], ["A", "B"], initial_idx=0, title="选择", bottom_bar=None,
        )
        assert result == {"action": "error", "index": None}
