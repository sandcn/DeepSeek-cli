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

    # ── 场景 5：React Ink 标准协议路径（ChatUI 活跃）回归 ──
    # 修复前：`deadline=time.monotonic() + 60` 中 `time` 未在方法内定义，
    # 且函数体后段存在 `import time` 使 `time` 被判定为局部变量，
    # 该路径抛 UnboundLocalError——正是 ChatUI 活跃时的常规路径。

    def _make_chat_ui_with_user_select(self):
        """构造 ChatUI 活跃场景：chat_ui.get_model() 返回带 user_select 的 model。

        注意：run_bottom_bar_selection 会替换 model.user_select 为新的
        UserSelectState，因此通过 time.sleep 的 side_effect 模拟
        UserSelectPopup 组件在轮询期间消费输入后写入 done/action。
        """
        model = MagicMock()
        chat_ui = MagicMock()
        chat_ui.get_model.return_value = model
        return chat_ui, model

    def test_react_ink_path_confirmed(self):
        """ChatUI 活跃 + model.user_select 存在 → 走 React Ink 路径并确认。"""
        adapter = CommandUiAdapter()
        chat_ui, model = self._make_chat_ui_with_user_select()

        def _confirm_on_sleep(sec):
            # 模拟 UserSelectPopup 组件消费输入后写入结果
            model.user_select.done = True
            model.user_select.action = "confirmed"
            model.user_select.selected = 1

        with patch.object(adapter, "_get_active_chat_ui", return_value=chat_ui):
            with patch("src.core.commands._ui_adapter.time.sleep",
                       side_effect=_confirm_on_sleep):
                result = adapter.run_bottom_bar_selection(
                    ["a", "b"], ["A", "B"], initial_idx=0, title="选择",
                )

        assert result == {"action": "confirmed", "index": 1}
        # user_select 状态在结束后应复位（新建空 UserSelectState）
        assert model.user_select.done is False
        assert model.user_select.visible is False

    def test_react_ink_path_cancel(self):
        """ChatUI 活跃但组件返回非 confirmed → 返回 cancel。"""
        adapter = CommandUiAdapter()
        chat_ui, model = self._make_chat_ui_with_user_select()

        def _cancel_on_sleep(sec):
            model.user_select.done = True
            model.user_select.action = "cancel"

        with patch.object(adapter, "_get_active_chat_ui", return_value=chat_ui):
            with patch("src.core.commands._ui_adapter.time.sleep",
                       side_effect=_cancel_on_sleep):
                result = adapter.run_bottom_bar_selection(
                    ["a", "b"], ["A", "B"], initial_idx=0, title="选择",
                )

        assert result == {"action": "cancel", "index": None}

    def test_react_ink_path_timeout(self):
        """轮询超时（deadline 已过且未 done）→ 置 timeout 并返回 cancel。"""
        adapter = CommandUiAdapter()
        chat_ui, model = self._make_chat_ui_with_user_select()

        # 第一次 monotonic 构造 deadline=160；第二次 > 160 → 立即超时
        with patch.object(adapter, "_get_active_chat_ui", return_value=chat_ui):
            with patch("src.core.commands._ui_adapter.time.monotonic",
                       side_effect=[100.0, 200.0]):
                with patch("src.core.commands._ui_adapter.time.sleep") as mock_sleep:
                    result = adapter.run_bottom_bar_selection(
                        ["a", "b"], ["A", "B"], initial_idx=0, title="选择",
                    )

        assert result == {"action": "cancel", "index": None}
        mock_sleep.assert_not_called()  # 首次轮询即超时，未 sleep
        # 结束后状态复位
        assert model.user_select.visible is False


class TestDisplayMessagesDelegation:
    """方向C 步骤4 输出路径统一回归测试。

    验证 CommandUiAdapter.display_messages：
      - ChatUI 活跃时委托 ChatUIConsumer.display_messages（路径 A）
      - ChatUI 不活跃时回退 pipeline/message_display 直写（非 ChatUI 兜底）
    """

    def test_display_messages_delegates_to_chat_ui_regression(self):
        """get_active_chat_ui 返回 consumer → display_messages 委托路径 A。"""
        from src.core.commands._ui_adapter import CommandUiAdapter

        adapter = CommandUiAdapter()
        data = [{"role": "user", "content": "hello"}]
        mock_consumer = MagicMock()

        with patch(
            "src.tui.consumer.get_active_chat_ui", return_value=mock_consumer,
        ):
            adapter.display_messages(data)

        mock_consumer.display_messages.assert_called_once_with(data, speed=0)

    def test_display_messages_delegates_preserves_speed_regression(self):
        """委托路径保留 speed 参数。"""
        from src.core.commands._ui_adapter import CommandUiAdapter

        adapter = CommandUiAdapter()
        data = [{"role": "assistant", "content": "hi"}]
        mock_consumer = MagicMock()

        with patch(
            "src.tui.consumer.get_active_chat_ui", return_value=mock_consumer,
        ):
            adapter.display_messages(data, speed=1000)

        mock_consumer.display_messages.assert_called_once_with(data, speed=1000)

    def test_display_messages_fallback_no_chat_ui_regression(self):
        """get_active_chat_ui 返回 None → 回退 pipeline 直写路径不抛异常。"""
        from src.core.commands._ui_adapter import CommandUiAdapter

        adapter = CommandUiAdapter()
        data = [{"role": "user", "content": "fallback"}]

        with patch(
            "src.tui.consumer.get_active_chat_ui", return_value=None,
        ) as mock_get:
            with patch(
                "src.tui.pipeline.message_display.display_messages",
            ) as mock_fn:
                adapter.display_messages(data)

        mock_get.assert_called_once()
        mock_fn.assert_called_once_with(
            data, agent=None, idx_map=None, speed=0,
        )
