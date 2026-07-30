"""测试 message_editor.py — 消息编辑器的消息选择交互。

测试 _interactive_message_select 在底部栏补全弹窗中的消息选择行为。
使用 unittest.mock 模拟 _BottomBar 和 Input，不执行真实终端 I/O。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.tui.pipeline.message_editor import MessageEditor


class TestInteractiveMessageSelect:
    """测试 _interactive_message_select 方法。"""

    def _make_mock_bottom_bar(self, completion_idx: int = 0) -> MagicMock:
        bb = MagicMock()
        bb.is_completion_visible.return_value = True
        bb.get_selected_completion_index.return_value = completion_idx
        return bb

    def _make_mock_input(self) -> MagicMock:
        inp = MagicMock()
        inp.interrupted = False
        return inp

    def _make_user_msgs(self, count: int = 3) -> list[tuple[int, dict]]:
        return [
            (i, {"role": "user", "content": f"message {i}"})
            for i in range(count)
        ]

    def _make_display_items(self, count: int = 3) -> list[str]:
        return [f"{i}. \u25cf \u2502 message {i}" for i in range(count)]

    # ── 场景 1：用户选中第 2 条消息（索引 1）后按 Enter ──

    @patch("src.tui.pipeline.message_editor.time.sleep")
    @patch("src.tui.pipeline.message_editor.time.monotonic")
    def test_interactive_message_select_selected_idx(
        self, mock_monotonic: MagicMock, mock_sleep: MagicMock,
    ):
        """用户选中第 2 条消息（索引 1）后按 Enter → 返回索引 1。"""
        bb = self._make_mock_bottom_bar(completion_idx=1)
        inp = self._make_mock_input()
        # 第1次: 无输入 → 进入 completion 检查；第2次: Enter → 退出循环
        inp.get_queued_input.side_effect = [None, "\n"]
        editor = MessageEditor(bottom_bar=bb, input_=inp)

        # deadline=100+120=220, 两次进入循环（120<220, 150<220），
        # 第2次循环中 get_queued_input 返回 "\n" 触发 break
        mock_monotonic.side_effect = [100.0, 120.0, 150.0]

        user_msgs = self._make_user_msgs(3)
        display_items = self._make_display_items(3)

        result = editor._interactive_message_select(user_msgs, display_items)

        assert result == 1  # 选中索引 1 的原始索引
        # ★ Enter 后在 visible 检查和 dismiss 后各读一次，共 2 次
        assert bb.get_selected_completion_index.call_count == 2
        bb.hide_completions.assert_called_once()

    # ── 场景 2：用户不操作直接 Enter → 默认最后一条 ──

    @patch("src.tui.pipeline.message_editor.time.sleep")
    @patch("src.tui.pipeline.message_editor.time.monotonic")
    def test_interactive_message_select_default_last(
        self, mock_monotonic: MagicMock, mock_sleep: MagicMock,
    ):
        """用户不选直接按 Enter → 返回最后一条消息索引。"""
        # 模拟真实行为：get_selected_completion_index 在 dismiss 后返回
        # _last_idx_before_hide（即 show_completions 设置的 sel_count-1 = 2）
        bb = self._make_mock_bottom_bar(completion_idx=2)
        inp = self._make_mock_input()
        inp.get_queued_input.return_value = "\n"  # 立即 Enter
        editor = MessageEditor(bottom_bar=bb, input_=inp)

        # deadline=100+120=220, 首次进入循环立即 break
        mock_monotonic.side_effect = [100.0, 200.0]

        user_msgs = self._make_user_msgs(3)
        display_items = self._make_display_items(3)

        result = editor._interactive_message_select(user_msgs, display_items)

        # Enter 后读取 get_selected_completion_index() 得到 2（模拟现实 _last_idx_before_hide）
        assert result == 2  # 默认最后一条消息的原始索引
        bb.get_selected_completion_index.assert_called_once()
        bb.hide_completions.assert_called_once()

    # ── 场景 3：异常导致取消 → 返回 None ──

    @patch("src.tui.pipeline.message_editor.time.sleep")
    @patch("src.tui.pipeline.message_editor.time.monotonic")
    def test_interactive_message_select_cancel(
        self, mock_monotonic: MagicMock, mock_sleep: MagicMock,
    ):
        """show_completions 抛出异常 → 返回 None。"""
        bb = self._make_mock_bottom_bar(completion_idx=0)
        inp = self._make_mock_input()
        # 让 show_completions 抛出异常，触发提前返回 None
        bb.show_completions.side_effect = RuntimeError("test error")
        editor = MessageEditor(bottom_bar=bb, input_=inp)

        mock_monotonic.side_effect = [100.0, 120.0]

        user_msgs = self._make_user_msgs(3)
        display_items = self._make_display_items(3)

        result = editor._interactive_message_select(user_msgs, display_items)

        assert result is None  # show_completions 失败返回 None
        bb.hide_completions.assert_not_called()  # 未显示弹窗，无需隐藏

    # ── 场景 4：用户按 ESC 取消 → 返回 None ──

    @patch("src.tui.pipeline.message_editor.time.sleep")
    @patch("src.tui.pipeline.message_editor.time.monotonic")
    def test_interactive_message_select_esc_cancel(
        self, mock_monotonic: MagicMock, mock_sleep: MagicMock,
    ):
        """用户按 ESC 中断选择 → 返回 None。"""
        bb = self._make_mock_bottom_bar(completion_idx=1)
        inp = self._make_mock_input()
        inp.get_queued_input.return_value = None  # 无 Enter
        inp.interrupted = True  # ESC 已按下
        editor = MessageEditor(bottom_bar=bb, input_=inp)

        mock_monotonic.side_effect = [100.0, 200.0]

        user_msgs = self._make_user_msgs(3)
        display_items = self._make_display_items(3)

        result = editor._interactive_message_select(user_msgs, display_items)

        assert result is None  # ESC 取消返回 None
        bb.hide_completions.assert_called_once()
