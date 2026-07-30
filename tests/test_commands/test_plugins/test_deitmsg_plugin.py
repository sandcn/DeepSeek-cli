"""测试 DeitmsgPlugin — /deitmsg 命令

覆盖场景：
1. _content_str 工具函数兼容 str/list[dict]
2. 插件注册正常
3. 通过 asyncio 运行 async_execute 验证核心逻辑
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from src.core.commands.plugins.deitmsg_plugin import DeitmsgPlugin, _content_str


class TestContentStr:
    """测试 _content_str 工具函数"""

    def test_str_content(self):
        """普通字符串直接返回"""
        assert _content_str("hello world") == "hello world"

    def test_list_dict_content(self):
        """list[dict] 格式提取 text 字段"""
        content = [{"type": "text", "text": "hello"}, {"type": "text", "text": "world"}]
        result = _content_str(content)
        assert "hello" in result
        assert "world" in result

    def test_list_dict_no_text(self):
        """list[dict] 不含 text 时 fallback"""
        content = [{"type": "image", "source": "url"}]
        result = _content_str(content)
        assert "url" in result

    def test_empty_content(self):
        """空字符串"""
        assert _content_str("") == ""

    def test_none_content(self):
        """content 为 None"""
        assert _content_str(None) == "None"


class TestDeitmsgPlugin:
    """测试 DeitmsgPlugin 核心逻辑"""

    def test_plugin_registered(self):
        """插件已正确注册"""
        from src.core.commands.plugins import get_interactive_registry

        reg = get_interactive_registry()
        plugin = reg.get("deitmsg")
        assert plugin is not None
        assert plugin.meta.name == "deitmsg"
        assert "编辑上一条" in plugin.meta.description

    @pytest.mark.asyncio
    async def test_no_user_msg_early_return(self):
        """无用户消息时提前返回，输出提示"""
        plugin = DeitmsgPlugin()
        chat_ui = MagicMock()
        loop = MagicMock()
        loop._chat_ui = chat_ui
        loop._monitor = MagicMock()
        plugin._loop = loop

        msgs = [{"role": "system", "content": "你是一个助手"}]
        session = MagicMock()
        session.messages = msgs
        session.captured_prefill = ""

        ctx = MagicMock()
        ctx.session = session
        ctx.state = {"model": "deepseek", "retry": False, "prefill": ""}

        result = await plugin.async_execute(ctx)

        assert result is True
        chat_ui.write_line.assert_called_once()
        args = chat_ui.write_line.call_args[0][0]
        assert "无用户消息" in args

    @pytest.mark.asyncio
    async def test_find_last_user_msg(self):
        """多条用户消息时定位到最后一条"""
        plugin = DeitmsgPlugin()
        chat_ui = MagicMock()
        loop = MagicMock()
        loop._chat_ui = chat_ui
        loop._monitor = MagicMock()
        plugin._loop = loop

        msgs = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "第一条"},
            {"role": "assistant", "content": "回复1"},
            {"role": "user", "content": "第二条"},
            {"role": "assistant", "content": "回复2"},
            {"role": "user", "content": "第三条"},
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
            "src.core.sandbox_manager.get_sandbox_manager",
            return_value=None,
        ):
            with patch(
                "src.app_loop._non_system_messages",
                return_value=msgs[1:],
            ):
                result = await plugin.async_execute(ctx)

        assert result is True
        # 截断后应剩 system + 第一条 + 回复1 + 第二条 + 回复2（5条）
        assert len(msgs) == 5, f"Expected 5 messages, got {len(msgs)}: {msgs}"
        assert msgs[-1]["content"] == "回复2"
        assert ctx.state["prefill"] == "第三条"

    @pytest.mark.asyncio
    async def test_sandbox_restore_and_prefill(self):
        """沙盒恢复 + 消息截断 + prefill + 显示还原数"""
        plugin = DeitmsgPlugin()
        chat_ui = MagicMock()
        loop = MagicMock()
        loop._chat_ui = chat_ui
        loop._monitor = MagicMock()
        plugin._loop = loop

        msgs = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "帮我改代码"},
            {"role": "assistant", "content": "好的"},
            {"role": "user", "content": "再改一下"},
        ]
        session = MagicMock()
        session.messages = msgs
        session.captured_prefill = ""
        session.sync_retry_pending = MagicMock()
        session.reset_retry_pending_for_edit = MagicMock()

        ctx = MagicMock()
        ctx.session = session
        ctx.state = {"model": "deepseek", "retry": False, "prefill": ""}

        mock_sandbox = MagicMock()
        mock_sandbox.restore_to_message.return_value = {
            "file1.py": True,
            "file2.py": True,
            "file3.py": False,
        }

        with patch(
            "src.core.sandbox_manager.get_sandbox_manager",
            return_value=mock_sandbox,
        ):
            with patch(
                "src.app_loop._non_system_messages",
                return_value=msgs[1:3],
            ):
                result = await plugin.async_execute(ctx)

        assert result is True
        # 验证沙盒恢复被调用，target_index = 2（最后一条 user 索引 3 - 1）
        mock_sandbox.restore_to_message.assert_called_once_with(2)
        mock_sandbox.remap_indices.assert_called_once()
        # 截断后应保留 system + "帮我改代码" + "好的"
        assert len(msgs) == 3
        assert ctx.state["prefill"] == "再改一下"
        # 验证显示了还原 2 个文件
        write_calls = [c[0][0] for c in chat_ui.write_line.call_args_list]
        restore_msg = [c for c in write_calls if "还原" in str(c)]
        assert len(restore_msg) > 0, f"No restore message found in: {write_calls}"
        assert "2" in str(restore_msg[0]), f"Expected '2' in '{restore_msg[0]}'"
        # 验证 post-finally 块中 flush 被调用
        # finally 块已有 1 次 flush + post-finally 块新增 1 次 = 共 2 次
        assert chat_ui.flush.call_count >= 2, (
            f"Expected flush >= 2 times, got {chat_ui.flush.call_count}"
        )
        # 验证调用顺序：display_messages 在 post-finally flush 之前
        display_idx = None
        flush_indices = []
        for idx, c in enumerate(chat_ui.mock_calls):
            cname = str(c)
            if "display_messages" in cname:
                display_idx = idx
            if "flush" in cname:
                flush_indices.append(idx)
        assert display_idx is not None, "display_messages not found in mock_calls"
        # 至少有 2 次 flush（finally + post-finally），其中至少一次在 display_messages 之后
        post_display_flushes = [i for i in flush_indices if i > display_idx]
        assert len(post_display_flushes) >= 1, (
            f"No flush after display_messages. flush_indices={flush_indices}, "
            f"display_idx={display_idx}, mock_calls={chat_ui.mock_calls}"
        )

    @pytest.mark.asyncio
    async def test_no_sandbox_manager(self):
        """无沙盒管理器时优雅降级"""
        plugin = DeitmsgPlugin()
        chat_ui = MagicMock()
        loop = MagicMock()
        loop._chat_ui = chat_ui
        loop._monitor = MagicMock()
        plugin._loop = loop

        msgs = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "测试"},
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
            "src.core.sandbox_manager.get_sandbox_manager",
            return_value=None,
        ):
            with patch(
                "src.app_loop._non_system_messages",
                return_value=[],
            ):
                result = await plugin.async_execute(ctx)

        assert result is True
        assert ctx.state["prefill"] == "测试"
        # 验证显示"无文件需还原"
        write_calls = [c[0][0] for c in chat_ui.write_line.call_args_list]
        assert any("无文件" in str(c) for c in write_calls)

    @pytest.mark.asyncio
    async def test_vision_content_compat(self):
        """content 为 list[dict]（vision 消息）兼容"""
        plugin = DeitmsgPlugin()
        chat_ui = MagicMock()
        loop = MagicMock()
        loop._chat_ui = chat_ui
        loop._monitor = MagicMock()
        plugin._loop = loop

        msgs = [
            {"role": "system", "content": "system"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "描述这张图片"},
                    {"type": "image_url", "image_url": {"url": "data:image/..."}},
                ],
            },
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
            "src.core.sandbox_manager.get_sandbox_manager",
            return_value=None,
        ):
            with patch(
                "src.app_loop._non_system_messages",
                return_value=[],
            ):
                result = await plugin.async_execute(ctx)

        assert result is True
        assert "描述这张图片" in ctx.state["prefill"]

    @pytest.mark.asyncio
    async def test_consecutive_user_messages(self):
        """连续 user 消息场景"""
        plugin = DeitmsgPlugin()
        chat_ui = MagicMock()
        loop = MagicMock()
        loop._chat_ui = chat_ui
        loop._monitor = MagicMock()
        plugin._loop = loop

        msgs = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "第一条"},
            {"role": "user", "content": "第二条（连续）"},
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
            "src.core.sandbox_manager.get_sandbox_manager",
            return_value=None,
        ):
            with patch(
                "src.app_loop._non_system_messages",
                return_value=[],
            ):
                result = await plugin.async_execute(ctx)

        assert result is True
        # 截断第二条 user 消息，保留第一条
        assert len(msgs) == 2
        assert msgs[-1]["content"] == "第一条"
        assert ctx.state["prefill"] == "第二条（连续）"

    @pytest.mark.asyncio
    async def test_empty_prefill_resets_retry_pending_regression(self):
        """空消息 prefill 场景 — 验证 reset_retry_pending_for_edit 被无条件调用

        Bug 场景：最后一条 user 消息 content 为空字符串 ""，
        sync_retry_pending 设置 retry_pending=True，旧代码的条件守卫
        (if state["prefill"] and not state["retry"]) 导致 reset 被跳过，
        prefill 为空时用户回车后触发自动 retry 而非显示预填内容。
        """
        plugin = DeitmsgPlugin()
        chat_ui = MagicMock()
        loop = MagicMock()
        loop._chat_ui = chat_ui
        loop._monitor = MagicMock()
        plugin._loop = loop

        msgs = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "第一条"},
            {"role": "assistant", "content": "回复1"},
            {"role": "user", "content": ""},  # 空消息
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
            "src.core.sandbox_manager.get_sandbox_manager",
            return_value=None,
        ):
            with patch(
                "src.app_loop._non_system_messages",
                return_value=[],
            ):
                result = await plugin.async_execute(ctx)

        assert result is True
        # prefill 应为空字符串
        assert ctx.state["prefill"] == ""
        # reset_retry_pending_for_edit 必须被调用（无条件重置）
        session.reset_retry_pending_for_edit.assert_called_once()
        # 验证传入了 has_prefill=False（空 prefill）
        call_kwargs = session.reset_retry_pending_for_edit.call_args[1]
        assert call_kwargs["has_prefill"] is False

    @pytest.mark.asyncio
    async def test_consecutive_user_messages_retry_pending_regression(self):
        """连续 user 消息截断后 retry_pending 状态验证

        Bug 场景：[system, user_A, user_B] 截断 user_B 后，
        messages[-1] 是 user（即 user_A），sync_retry_pending 推导
        retry_pending=True，但 reset_retry_pending_for_edit 应覆盖为 False。
        验证截断后 retry_pending 保持 False（不被 sync 推导后残留为 True）。
        """
        plugin = DeitmsgPlugin()
        chat_ui = MagicMock()
        loop = MagicMock()
        loop._chat_ui = chat_ui
        loop._monitor = MagicMock()
        plugin._loop = loop

        msgs = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "消息A"},
            {"role": "user", "content": "消息B（连续）"},
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
            "src.core.sandbox_manager.get_sandbox_manager",
            return_value=None,
        ):
            with patch(
                "src.app_loop._non_system_messages",
                return_value=[],
            ):
                result = await plugin.async_execute(ctx)

        assert result is True
        # 截断后应剩 [system, user_A]
        assert len(msgs) == 2
        assert msgs[-1]["content"] == "消息A"
        assert ctx.state["prefill"] == "消息B（连续）"
        # sync_retry_pending 被调用
        session.sync_retry_pending.assert_called_once()
        # reset_retry_pending_for_edit 在 sync 之后调用，覆盖为 False
        session.reset_retry_pending_for_edit.assert_called_once()
        # 验证调用顺序：sync → reset（严格顺序）
        session.assert_has_calls(
            [call.sync_retry_pending(), call.reset_retry_pending_for_edit(has_prefill=True)],
            any_order=False,
        )

    @pytest.mark.asyncio
    async def test_post_finally_flush_called(self):
        """验证 post-finally 块中 flush 被调用，即使 display_messages 抛异常"""
        plugin = DeitmsgPlugin()
        chat_ui = MagicMock()
        # display_messages 抛出异常但 flush 仍应被调用
        chat_ui.display_messages.side_effect = RuntimeError("render error")
        loop = MagicMock()
        loop._chat_ui = chat_ui
        loop._monitor = MagicMock()
        plugin._loop = loop

        msgs = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "测试消息"},
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
            "src.core.sandbox_manager.get_sandbox_manager",
            return_value=None,
        ):
            with patch(
                "src.app_loop._non_system_messages",
                return_value=msgs[1:],
            ):
                result = await plugin.async_execute(ctx)

        assert result is True
        # display_messages 被调用但抛异常
        chat_ui.display_messages.assert_called_once()
        # 验证 flush 仍被调用（finally + post-finally 共 2 次）
        assert chat_ui.flush.call_count >= 2, (
            f"Expected flush >= 2 even when display_messages fails, "
            f"got {chat_ui.flush.call_count}"
        )
        # 验证 post-finally flush 在 display_messages 之后调用
        display_idx = None
        flush_indices = []
        for idx, c in enumerate(chat_ui.mock_calls):
            cname = str(c)
            if "display_messages" in cname:
                display_idx = idx
            if "flush" in cname:
                flush_indices.append(idx)
        assert display_idx is not None
        post_display_flushes = [i for i in flush_indices if i > display_idx]
        assert len(post_display_flushes) >= 1, (
            "flush not called after display_messages failed"
        )
