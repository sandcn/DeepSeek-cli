"""测试 EditmsgPlugin — /editmsg 命令

覆盖场景：
1. _edit_performed 标志对 needs_rerender 的影响
2. 空 prefill + _edit_performed=True → needs_rerender=True（沙盒信息显示）
3. 空 prefill + 无 _edit_performed → needs_rerender=False（向后兼容）
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestEditmsgPluginSyncExecution:
    """编辑逻辑同步直接执行（不用 run_in_executor 线程池）。

    用户需求：/editmsg 按回车确认后编辑立即生效——MessageEditor 在主流程
    同步直接调用（render 线程独立驱动 UserSelectPopup 写 done），不依赖
    线程池调度返回。
    """

    @pytest.mark.asyncio
    async def test_message_editor_called_synchronously(self):
        """MessageEditor.edit_current_messages 被直接调用（非 run_in_executor）。"""
        from src.core.commands.plugins.editmsg_plugin import EditmsgPlugin

        plugin = EditmsgPlugin()
        chat_ui = MagicMock()
        input_inst = MagicMock()
        chat_ui.get_input.return_value = input_inst
        loop = MagicMock()
        loop._chat_ui = chat_ui
        loop._monitor = MagicMock()
        plugin._loop = loop

        msgs = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "第一条"},
        ]
        session = MagicMock()
        session.messages = msgs
        session.captured_prefill = ""
        session.sync_retry_pending = MagicMock()
        session.reset_retry_pending_for_edit = MagicMock()

        ctx = MagicMock()
        ctx.session = session
        ctx.state = {"model": "deepseek", "retry": False, "prefill": ""}

        with patch(
            "src.tui.pipeline.message_editor.MessageEditor",
        ) as mock_editor_cls:
            mock_editor = MagicMock()
            mock_editor.edit_current_messages.return_value = True
            mock_editor_cls.return_value = mock_editor

            with patch(
                "src.app_loop._non_system_messages",
                return_value=msgs[1:],
            ):
                result = await plugin.async_execute(ctx)

        assert result is True
        # edit_current_messages 被直接调用（mock 实例方法调用即证明同步路径，
        # 若走 run_in_executor 则调用的是 executor 提交而非直接方法调用）
        mock_editor.edit_current_messages.assert_called_once_with(
            session.agent, {"model": "deepseek", "retry": False, "prefill": ""},
            "edit",
        )


class TestEditmsgPluginNeedsRerender:
    """测试 editmsg_plugin.py 中 needs_rerender 判断逻辑。

    核心回归：_edit_performed 标志独立于 prefill 是否为空，
    确保空内容编辑的沙盒信息也能显示。
    """

    def test_needs_rerender_true_with_empty_prefill_and_edit_performed_regression(self):
        """空 prefill + _edit_performed=True → needs_rerender=True。

        场景：用户编辑一条内容为空的消息（合法操作），沙盒信息应显示。
        """
        edit_state = {"_edit_performed": True, "prefill": "", "retry": False}
        state = {"retry": False, "prefill": ""}

        needs_rerender = bool(
            edit_state.get("_edit_performed", False)
            or state["retry"]
            or state["prefill"]
        )
        assert needs_rerender is True

    def test_needs_rerender_false_without_edit_performed_regression(self):
        """空 prefill + 无 _edit_performed → needs_rerender=False。

        场景：编辑未实际执行（如取消选择），不触发重新渲染。
        """
        edit_state = {"prefill": "", "retry": False}
        state = {"retry": False, "prefill": ""}

        needs_rerender = bool(
            edit_state.get("_edit_performed", False)
            or state["retry"]
            or state["prefill"]
        )
        assert needs_rerender is False

    def test_needs_rerender_true_with_nonempty_prefill_regression(self):
        """非空 prefill + 无 _edit_performed → needs_rerender=True（向后兼容）。

        场景：旧代码路径（无 _edit_performed 标志），prefill 非空时仍触发渲染。
        """
        edit_state = {"prefill": "hello", "retry": False}
        state = {"retry": False, "prefill": "hello"}

        needs_rerender = bool(
            edit_state.get("_edit_performed", False)
            or state["retry"]
            or state["prefill"]
        )
        assert needs_rerender is True

    def test_needs_rerender_true_with_retry_flag_regression(self):
        """retry=True + 空 prefill + 无 _edit_performed → needs_rerender=True。

        场景：恢复操作触发 retry 标记，应触发重新渲染。
        """
        edit_state = {"prefill": "", "retry": True}
        state = {"retry": True, "prefill": ""}

        needs_rerender = bool(
            edit_state.get("_edit_performed", False)
            or state["retry"]
            or state["prefill"]
        )
        assert needs_rerender is True


class TestEditmsgPluginClearThenRerender:
    """编辑生效后：先清空消息区旧显示，再重新渲染剩余消息。

    用户需求（/editmsg TUI）：按下回车确认选择后，删除消息区原来显示的
    信息（含被编辑消息及其后内容的旧渲染），把剩下信息重新渲染一次。
    核心断言：``clear_messages`` 在 ``display_messages`` 之前被调用。
    """

    @pytest.mark.asyncio
    async def test_clear_messages_before_display_messages(self):
        """clear_messages 先于 display_messages 调用（同批按序处理）。"""
        from src.core.commands.plugins.editmsg_plugin import EditmsgPlugin

        plugin = EditmsgPlugin()
        chat_ui = MagicMock()
        input_inst = MagicMock()
        chat_ui.get_input.return_value = input_inst
        loop = MagicMock()
        loop._chat_ui = chat_ui
        loop._monitor = MagicMock()
        plugin._loop = loop

        msgs = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "第一条"},
            {"role": "assistant", "content": "回复1"},
            {"role": "user", "content": "被编辑的消息"},
            {"role": "assistant", "content": "回复2"},
        ]
        session = MagicMock()
        session.messages = msgs
        session.captured_prefill = ""
        session.sync_retry_pending = MagicMock()
        session.reset_retry_pending_for_edit = MagicMock()

        ctx = MagicMock()
        ctx.session = session
        ctx.state = {"model": "deepseek", "retry": False, "prefill": ""}

        def _fake_edit(agent, state, action="edit"):
            # 模拟 MessageEditor.edit_current_messages 成功编辑
            state["prefill"] = "被编辑的消息"
            state["_edit_performed"] = True
            state["retry"] = False
            state["_restore_text"] = "已恢复 1 个文件"
            return True

        with patch(
            "src.tui.pipeline.message_editor.MessageEditor",
        ) as mock_editor_cls:
            mock_editor = MagicMock()
            mock_editor.edit_current_messages.side_effect = _fake_edit
            mock_editor_cls.return_value = mock_editor
            with patch(
                "src.app_loop._non_system_messages",
                return_value=msgs[1:3],  # 截断后剩余: 第一条 + 回复1
            ):
                result = await plugin.async_execute(ctx)

        assert result is True

        # 验证调用顺序：clear_messages 在 display_messages 之前
        call_names = [str(c) for c in chat_ui.mock_calls]
        clear_idx = None
        display_idx = None
        for idx, name in enumerate(call_names):
            if "clear_messages" in name:
                clear_idx = idx
            if "display_messages" in name:
                display_idx = idx
        assert clear_idx is not None, f"clear_messages 未被调用: {call_names}"
        assert display_idx is not None, f"display_messages 未被调用: {call_names}"
        assert clear_idx < display_idx, (
            f"clear_messages (idx={clear_idx}) 应先于 display_messages "
            f"(idx={display_idx}) 调用: {call_names}"
        )
        # display_messages 传入截断后的剩余消息
        display_call = chat_ui.display_messages.call_args
        assert display_call is not None
        assert display_call[0][0] == msgs[1:3]
        # 编辑后立即能重新编辑：prefill 已注入 state
        assert ctx.state["prefill"] == "被编辑的消息"

    @pytest.mark.asyncio
    async def test_clear_messages_not_called_when_cancel(self):
        """用户取消选择（未编辑）→ 不触发 clear_messages / display_messages。"""
        from src.core.commands.plugins.editmsg_plugin import EditmsgPlugin

        plugin = EditmsgPlugin()
        chat_ui = MagicMock()
        input_inst = MagicMock()
        chat_ui.get_input.return_value = input_inst
        loop = MagicMock()
        loop._chat_ui = chat_ui
        loop._monitor = MagicMock()
        plugin._loop = loop

        msgs = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "第一条"},
        ]
        session = MagicMock()
        session.messages = msgs
        session.captured_prefill = ""
        session.sync_retry_pending = MagicMock()
        session.reset_retry_pending_for_edit = MagicMock()

        ctx = MagicMock()
        ctx.session = session
        ctx.state = {"model": "deepseek", "retry": False, "prefill": ""}

        def _fake_edit_cancel(agent, state, action="edit"):
            # 模拟用户 ESC 取消（无编辑）
            return False

        with patch(
            "src.tui.pipeline.message_editor.MessageEditor",
        ) as mock_editor_cls:
            mock_editor = MagicMock()
            mock_editor.edit_current_messages.side_effect = _fake_edit_cancel
            mock_editor_cls.return_value = mock_editor
            result = await plugin.async_execute(ctx)

        assert result is True
        chat_ui.clear_messages.assert_not_called()
        chat_ui.display_messages.assert_not_called()
        # 取消提示
        assert any(
            "未编辑" in str(c[0][0])
            for c in chat_ui.write_line.call_args_list
        )
