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
        """用户选中第 2 条消息（索引 1）后按 Enter → 返回索引 1。

        新检测机制：Enter 通过 _selection_ready Event 信号检测，
        不再经过 get_queued_input / _enter 路径。
        """
        bb = self._make_mock_bottom_bar(completion_idx=1)
        inp = self._make_mock_input()
        editor = MessageEditor(bottom_bar=bb, input_=inp)
        # 模拟 Enter 按键：设置 _selection_ready 信号
        editor._selection_ready.set()

        # 首次进入循环，_selection_ready.wait(timeout=0.05) 立即返回 True
        mock_monotonic.side_effect = [100.0, 200.0]

        user_msgs = self._make_user_msgs(3)
        display_items = self._make_display_items(3)

        result = editor._interactive_message_select(user_msgs, display_items)

        assert result == 1  # 选中索引 1 的原始索引
        # Enter 检测路径中调用一次 get_selected_completion_index
        bb.get_selected_completion_index.assert_called_once()
        bb.hide_completions.assert_called_once()

    # ── 场景 2：用户不操作直接 Enter → 默认最后一条 ──

    @patch("src.tui.pipeline.message_editor.time.sleep")
    @patch("src.tui.pipeline.message_editor.time.monotonic")
    def test_interactive_message_select_default_last(
        self, mock_monotonic: MagicMock, mock_sleep: MagicMock,
    ):
        """用户不选直接按 Enter → 返回最后一条消息索引。

        新检测机制：_selection_ready Event 信号驱动，
        get_selected_completion_index 在 dismiss 后返回
        _last_idx_before_hide（即 show_completions 设置的 sel_count-1 = 2）。
        """
        bb = self._make_mock_bottom_bar(completion_idx=2)
        inp = self._make_mock_input()
        editor = MessageEditor(bottom_bar=bb, input_=inp)
        # 模拟 Enter 按键：设置 _selection_ready 信号
        editor._selection_ready.set()

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
        inp.interrupted = True  # ESC 已按下
        editor = MessageEditor(bottom_bar=bb, input_=inp)

        mock_monotonic.side_effect = [100.0, 200.0]

        user_msgs = self._make_user_msgs(3)
        display_items = self._make_display_items(3)

        result = editor._interactive_message_select(user_msgs, display_items)

        assert result is None  # ESC 取消返回 None
        bb.hide_completions.assert_called_once()

    # ── 场景 5：竞态条件 Enter + interrupted 同时触发 → Enter 优先（回归测试）──

    @patch("src.tui.pipeline.message_editor.time.sleep")
    @patch("src.tui.pipeline.message_editor.time.monotonic")
    def test_interactive_message_select_enter_priority_over_interrupted_regression(
        self, mock_monotonic: MagicMock, mock_sleep: MagicMock,
    ):
        """Enter 和 interrupted 同时触发 → Enter 优先，返回选中索引。

        新检测机制：_selection_ready.wait() 检测 Enter 优先于 input_.interrupted。
        """
        bb = self._make_mock_bottom_bar(completion_idx=1)
        inp = self._make_mock_input()
        # 竞态条件：Enter 信号和 ESC 中断同时触发
        editor = MessageEditor(bottom_bar=bb, input_=inp)
        editor._selection_ready.set()
        inp.interrupted = True

        mock_monotonic.side_effect = [100.0, 200.0]

        user_msgs = self._make_user_msgs(3)
        display_items = self._make_display_items(3)

        result = editor._interactive_message_select(user_msgs, display_items)

        # Enter 优先，返回选中索引而非 None（_selection_ready.wait 在 interrupted 之前被检查）
        assert result == 1
        bb.get_selected_completion_index.assert_called_once()
        bb.hide_completions.assert_called_once()


class TestRestoreSandboxTo:
    """测试 _restore_sandbox_to 异常保护 — 沙盒恢复失败不阻断编辑流程。"""

    @patch("src.tui.pipeline.message_editor._get_sandbox_manager")
    def test_restore_sandbox_to_exception_no_block_regression(
        self, mock_get_sm: MagicMock,
    ):
        """_restore_sandbox_to 中 restore_to_message 抛异常 → 不重新抛出，返回描述文本。"""
        from src.tui.pipeline.message_editor import _restore_sandbox_to

        sm = MagicMock()
        sm.restore_to_message.side_effect = RuntimeError("模拟沙盒恢复异常")
        mock_get_sm.return_value = sm

        result = _restore_sandbox_to(agent=None, target_idx=3)

        assert "沙盒恢复失败" in result
        # 不抛出异常（函数正常返回即证明）

    @patch("src.tui.pipeline.message_editor._get_sandbox_manager")
    def test_edit_command_prefill_still_set_when_restore_fails_regression(
        self, mock_get_sm: MagicMock,
    ):
        """EditCommand.execute 中沙盒恢复失败时 prefill 仍被设置，编辑流程不中断。"""
        from src.tui.pipeline.message_editor import EditCommand

        sm = MagicMock()
        sm.restore_to_message.side_effect = RuntimeError("模拟沙盒恢复异常")
        # ★ sm.remap_indices 也需要 mock，否则测试会因 MagicMock 默认行为而静默通过
        sm.remap_indices = MagicMock()
        mock_get_sm.return_value = sm

        # 构造 mock agent，含 messages 列表
        agent = MagicMock()
        agent.messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "old message content"},
        ]

        cmd = EditCommand(agent, real_idx=3)
        state: dict = {}
        result = cmd.execute(state)

        assert result is True
        assert state.get("prefill") == "old message content"
        assert state.get("_restore_text", "").startswith("沙盒恢复失败")
        # 消息已被截断：4 条 → 3 条（real_idx=3 被截断）
        assert len(agent.messages) == 3


class TestEditCurrentMessagesFinallyOrder:
    """测试 edit_current_messages() 中 finally 块的执行顺序。

    回归测试：验证 flush_stdin_buffer() 在 set_suppress_enter(False) 之前调用，
    防止 CR+LF 竞态导致 \n 残留字节在 Enter 抑制恢复后被 render 线程读取触发
    _enter() 误消费 prefill。
    """

    @patch("src.tui.pipeline.message_editor._get_sandbox_manager")
    def test_flush_stdin_buffer_before_set_suppress_enter_regression(
        self, mock_get_sm: MagicMock,
    ):
        """验证 finally 块中 flush_stdin_buffer 先于 set_suppress_enter(False) 调用。"""
        import threading

        from src.tui.pipeline.message_editor import MessageEditor

        # 构造 mock sandbox manager（避免 None 导致的副作用）
        sm = MagicMock()
        sm.restore_to_message.return_value = {}
        sm.remap_indices = MagicMock()
        mock_get_sm.return_value = sm

        # 构造 mock Input，带调用顺序记录
        call_order: list[str] = []

        inp = MagicMock()
        inp.set_suppress_enter = MagicMock(
            side_effect=lambda *a: call_order.append("set_suppress_enter")
        )
        inp.flush_stdin_buffer = MagicMock(
            side_effect=lambda *a: call_order.append("flush_stdin_buffer")
        )

        # 构造 mock BottomBar
        bb = MagicMock()

        # 构造 mock agent
        agent = MagicMock()
        agent.messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "hello"},
        ]

        editor = MessageEditor(bottom_bar=bb, input_=inp)

        # Mock _interactive_message_select 返回有效索引
        with patch.object(
            editor, "_interactive_message_select", return_value=1,
        ):
            state: dict = {}
            result = editor.edit_current_messages(agent, state, action="edit")

        assert result is True

        # 验证调用顺序：flush_stdin_buffer 在 finally 块中的 set_suppress_enter(False) 之前
        # 注意：set_suppress_enter 被调用两次（True → False），我们关心的是 finally 块中的第二次
        flush_idx = call_order.index("flush_stdin_buffer") if "flush_stdin_buffer" in call_order else -1
        # 获取 set_suppress_enter 的所有出现位置
        suppress_indices = [
            i for i, name in enumerate(call_order) if name == "set_suppress_enter"
        ]
        assert flush_idx >= 0, f"flush_stdin_buffer 未被调用, call_order={call_order}"
        assert len(suppress_indices) >= 2, (
            f"set_suppress_enter 应被调用两次（True + False），实际: {call_order}"
        )
        # finally 块中的 set_suppress_enter(False) 是第二次调用
        suppress_false_idx = suppress_indices[1]
        assert flush_idx < suppress_false_idx, (
            f"flush_stdin_buffer (idx={flush_idx}) 应在 finally 块中的 "
            f"set_suppress_enter(False) (idx={suppress_false_idx}) 之前调用, "
            f"call_order={call_order}"
        )


class TestEditPerformedFlag:
    """测试四个命令类正确设置 _edit_performed 标志。"""

    @patch("src.tui.pipeline.message_editor._get_sandbox_manager")
    def test_edit_command_sets_edit_performed_flag_regression(
        self, mock_get_sm: MagicMock,
    ):
        """EditCommand.execute() 成功后 state["_edit_performed"] 为 True。"""
        from src.tui.pipeline.message_editor import EditCommand

        sm = MagicMock()
        sm.restore_to_message.return_value = {}
        sm.remap_indices = MagicMock()
        mock_get_sm.return_value = sm

        agent = MagicMock()
        agent.messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "hello"},
        ]

        cmd = EditCommand(agent, real_idx=1)
        state: dict = {}
        result = cmd.execute(state)

        assert result is True
        assert state["_edit_performed"] is True

    @patch("src.tui.pipeline.message_editor._get_sandbox_manager")
    def test_delete_command_sets_edit_performed_flag_regression(
        self, mock_get_sm: MagicMock,
    ):
        """DeleteCommand.execute() 成功后 state["_edit_performed"] 为 True。"""
        from src.tui.pipeline.message_editor import DeleteCommand

        sm = MagicMock()
        sm.restore_to_message.return_value = {}
        sm.remap_indices = MagicMock()
        mock_get_sm.return_value = sm

        agent = MagicMock()
        agent.messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "hello"},
        ]

        cmd = DeleteCommand(agent, real_idx=1)
        state: dict = {}
        result = cmd.execute(state)

        assert result is True
        assert state["_edit_performed"] is True

    @patch("src.tui.pipeline.message_editor._get_sandbox_manager")
    def test_resume_command_sets_edit_performed_flag_regression(
        self, mock_get_sm: MagicMock,
    ):
        """ResumeCommand.execute() 成功后 state["_edit_performed"] 为 True。"""
        from src.tui.pipeline.message_editor import ResumeCommand

        sm = MagicMock()
        sm.restore_to_message.return_value = {}
        sm.remap_indices = MagicMock()
        mock_get_sm.return_value = sm

        agent = MagicMock()
        agent.messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]

        cmd = ResumeCommand(agent, real_idx=1)
        state: dict = {}
        result = cmd.execute(state)

        assert result is True
        assert state["_edit_performed"] is True

    @patch("src.tui.pipeline.message_editor._get_sandbox_manager")
    def test_resume_all_command_sets_edit_performed_flag_regression(
        self, mock_get_sm: MagicMock,
    ):
        """ResumeAllCommand.execute() 成功后 state["_edit_performed"] 为 True。"""
        from src.tui.pipeline.message_editor import ResumeAllCommand

        sm = MagicMock()
        mock_get_sm.return_value = sm

        agent = MagicMock()
        agent.messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]

        cmd = ResumeAllCommand(agent)
        state: dict = {}
        result = cmd.execute(state)

        assert result is True
        assert state["_edit_performed"] is True
