"""测试 EditmsgPlugin — 编辑消息后 prefill 与 retry_pending 的交互

修复场景: 用户通过 /editmsg 选择消息编辑后，
若截断后最后一条消息角色为 user（如连续两条 user 消息），
sync_retry_pending() 会设 retry_pending=True，导致下轮
_handle_round 走 retry 路径吞掉 prefill（预填内容不在编辑行显示）。

测试策略:
- 使用 mock 替待 EditmsgPlugin 的外部依赖（loop/chat_ui/monitor/session/agent）
- 直接验证 async_execute 调用后 ctx.state 和 session.retry_pending 的状态
"""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import MagicMock, PropertyMock, patch


# ═══════════════════════════════════════════════════════════════════════════
#  Fixtures
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def mock_session():
    """创建 mock ChatSession，支持 sync_retry_pending / reset_retry_pending

    默认包含一条 user 消息，使 /editmsg 预检查通过（有可编辑的用户消息）。
    测试需模拟"无 user 消息"场景时单独设置 session.messages = []。
    """
    session = MagicMock()
    session.sync_retry_pending = MagicMock()
    session.reset_retry_pending = MagicMock()
    session.agent = MagicMock()
    session.agent.messages = []
    session.messages = [{"role": "user", "content": "hello"}]
    return session


@pytest.fixture
def mock_loop():
    """创建 mock InteractiveLoop"""
    loop = MagicMock()
    loop._chat_ui = MagicMock()
    loop._monitor = MagicMock()
    return loop


@pytest.fixture
def mock_ctx(mock_session):
    """创建 mock CommandContext"""
    ctx = MagicMock()
    ctx.session = mock_session
    ctx.state = {"model": "gpt-4o", "retry": False, "prefill": ""}
    return ctx


# ═══════════════════════════════════════════════════════════════════════════
#  导入 EditmsgPlugin（延迟导入避免模块加载副作用）
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def EditmsgPlugin():
    from src.core.commands.plugins.editmsg_plugin import EditmsgPlugin
    return EditmsgPlugin


# ═══════════════════════════════════════════════════════════════════════════
#  测试：retry_pending 重置逻辑
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_prefill_resets_retry_pending(EditmsgPlugin, mock_loop, mock_ctx, mock_session):
    """有 prefill 且非 retry 模式时，retry_pending 被重置为 False"""
    plugin = EditmsgPlugin()
    plugin.bind_loop(mock_loop)

    # 模拟 edit_current_messages 修改 edit_state 设 prefill
    with patch(
        "src.tui.pipeline.message_editor.edit_current_messages",
        new=lambda agent, state: state.update({"prefill": "old user content", "retry": False}),
    ):
        result = await plugin.async_execute(mock_ctx)

    assert result is True
    assert mock_ctx.state["prefill"] == ""  # 已被 finally 清空（monitor.start 已消费）
    assert mock_ctx.state["retry"] is False
    # monitor.start 被调用，prefill 参数已被消费
    mock_loop._monitor.start.assert_called_once_with(prefill="old user content")
    # sync_retry_pending 被调用过
    mock_session.sync_retry_pending.assert_called_once()
    # reset_retry_pending 被调用过（prefill 有值 && retry=False）
    mock_session.reset_retry_pending.assert_called_once()


@pytest.mark.asyncio
async def test_retry_mode_keeps_retry_pending(EditmsgPlugin, mock_loop, mock_ctx, mock_session):
    """有 prefill 但 retry=True 时（主动 retry 模式），retry_pending 不被重置"""
    plugin = EditmsgPlugin()
    plugin.bind_loop(mock_loop)

    # 模拟 edit_state 中 retry=True
    with patch(
        "src.tui.pipeline.message_editor.edit_current_messages",
        new=lambda agent, state: state.update({"prefill": "old content", "retry": True}),
    ):
        result = await plugin.async_execute(mock_ctx)

    assert result is True
    assert mock_ctx.state["prefill"] == ""  # 已被 finally 清空
    assert mock_ctx.state["retry"] is True
    # monitor.start 被调用，prefill 参数已被消费
    mock_loop._monitor.start.assert_called_once_with(prefill="old content")
    mock_session.sync_retry_pending.assert_called_once()
    # retry=True → 不调用 reset_retry_pending
    mock_session.reset_retry_pending.assert_not_called()


@pytest.mark.asyncio
async def test_no_prefill_keeps_retry_pending(EditmsgPlugin, mock_loop, mock_ctx, mock_session):
    """无 prefill 时（用户取消编辑），retry_pending 不被重置"""
    plugin = EditmsgPlugin()
    plugin.bind_loop(mock_loop)

    # 模拟 edit_state 中 prefill 为空（无编辑动作）
    with patch(
        "src.tui.pipeline.message_editor.edit_current_messages",
        new=lambda agent, state: state.update({"prefill": "", "retry": False}),
    ):
        result = await plugin.async_execute(mock_ctx)

    assert result is True
    assert mock_ctx.state["prefill"] == ""
    assert mock_ctx.state["retry"] is False
    # finally 块中 monitor.start 被调用（prefill 为空）
    mock_loop._monitor.start.assert_called_once_with(prefill="")
    mock_session.sync_retry_pending.assert_called_once()
    # 无 prefill → 不调用 reset_retry_pending
    mock_session.reset_retry_pending.assert_not_called()


@pytest.mark.asyncio
async def test_no_user_msg_returns_true(EditmsgPlugin, mock_loop, mock_ctx, mock_session):
    """会话无 user 消息时，提前返回 True（命令已被识别并处理）且不调用 suspend/stop

    返回 True 阻止 _handle_command_msg 的 else 分支输出"未知命令"误导提示。
    其他插件（LoopPlugin/ModelPlugin）在参数校验失败路径也返回 True。
    """
    plugin = EditmsgPlugin()
    plugin.bind_loop(mock_loop)
    mock_session.messages = [{"role": "assistant", "content": "hi"}]

    result = await plugin.async_execute(mock_ctx)

    assert result is True
    # 不应进入编辑交互（无 user 消息）
    mock_session.sync_retry_pending.assert_not_called()
    mock_session.reset_retry_pending.assert_not_called()
    # 不应调用 suspend/stop（预检查在之前返回）
    mock_loop._chat_ui.suspend.assert_not_called()
    mock_loop._monitor.stop.assert_not_called()


@pytest.mark.asyncio
async def test_loop_none_returns_false(EditmsgPlugin, mock_ctx):
    """loop 为 None 时返回 False（未绑定 InteractiveLoop）"""
    plugin = EditmsgPlugin()
    plugin._loop = None  # 未绑定 loop
    result = await plugin.async_execute(mock_ctx)
    assert result is False


@pytest.mark.asyncio
async def test_sync_retry_pending_called_after_edit(EditmsgPlugin, mock_loop, mock_ctx, mock_session):
    """sync_retry_pending 在编辑之后被调用（验证调用顺序正确）"""
    plugin = EditmsgPlugin()
    plugin.bind_loop(mock_loop)

    # 记录调用顺序
    call_log = []

    def tracked_edit(agent, state):
        call_log.append("edit")
        state.update({"prefill": "content", "retry": False})

    # 让 sync_retry_pending 使用真正的 side_effect 记录
    mock_session.sync_retry_pending.side_effect = lambda: call_log.append("sync_retry")
    mock_session.reset_retry_pending.side_effect = lambda: call_log.append("reset_retry")

    with patch(
        "src.tui.pipeline.message_editor.edit_current_messages",
        new=tracked_edit,
    ):
        result = await plugin.async_execute(mock_ctx)

    assert result is True
    # 调用顺序必须是: edit → sync_retry → reset_retry
    assert call_log == ["edit", "sync_retry", "reset_retry"], (
        f"调用顺序异常: {call_log}"
    )
    # finally 块中 monitor.start 被调用，prefill 已被消费
    mock_loop._monitor.start.assert_called_once_with(prefill="content")


@pytest.mark.asyncio
async def test_needs_rerender_with_prefill(EditmsgPlugin, mock_loop, mock_ctx, mock_session):
    """有 prefill 或 retry 时标记 needs_rerender，触发 display_messages"""
    plugin = EditmsgPlugin()
    plugin.bind_loop(mock_loop)

    with patch(
        "src.tui.pipeline.message_editor.edit_current_messages",
        new=lambda agent, state: state.update({"prefill": "content", "retry": False}),
    ):
        await plugin.async_execute(mock_ctx)

    # chat_ui.display_messages 应该被调用（needs_rerender=True）
    mock_loop._chat_ui.display_messages.assert_called_once()


@pytest.mark.asyncio
async def test_no_rerender_without_prefill(EditmsgPlugin, mock_loop, mock_ctx, mock_session):
    """无 prefill 且无 retry 时不触发重渲染"""
    plugin = EditmsgPlugin()
    plugin.bind_loop(mock_loop)

    with patch(
        "src.tui.pipeline.message_editor.edit_current_messages",
        new=lambda agent, state: state.update({"prefill": "", "retry": False}),
    ):
        await plugin.async_execute(mock_ctx)

    # chat_ui.display_messages 不应该被调用（needs_rerender=False）
    mock_loop._chat_ui.display_messages.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════
#  测试：finally 块 flush 调用 + 调用顺序
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_finally_block_flush_before_monitor_start(EditmsgPlugin, mock_loop, mock_ctx):
    """验证 finally 块调用顺序: chat_ui.resume → chat_ui.flush → monitor.start(prefill=...) → chat_ui.flush

    使用 call_log 模式记录跨 mock 对象的调用时序：
    1. chat_ui.resume() 最先（恢复渲染线程）
    2. chat_ui.flush() 其次（等待 render 线程就绪）
    3. monitor.start(prefill=...) 再次（render 已就绪，安全设置 prefill）
    4. chat_ui.flush() 最后（处理 monitor.start 产生的渲染命令）
    """
    plugin = EditmsgPlugin()
    plugin.bind_loop(mock_loop)

    # 使用 call_log 记录跨 mock 的调用顺序
    call_log = []
    mock_loop._chat_ui.resume.side_effect = lambda: call_log.append("resume")
    mock_loop._monitor.start.side_effect = lambda prefill: call_log.append(f"start:{prefill}")
    mock_loop._chat_ui.flush.side_effect = lambda: call_log.append("flush")

    with patch(
        "src.tui.pipeline.message_editor.edit_current_messages",
        new=lambda agent, state: state.update({"prefill": "content", "retry": False}),
    ):
        await plugin.async_execute(mock_ctx)

    # 验证调用顺序 + prefill 参数: resume → flush → start:content → flush
    assert call_log == ["resume", "flush", "start:content", "flush"], (
        f"finally 块调用顺序或 prefill 参数异常，期望 ['resume', 'flush', 'start:content', 'flush']，实际 {call_log}"
    )
    # flush 恰好被调用两次（resume 后一次等待 render 就绪，monitor.start 后一次处理渲染命令）
    assert mock_loop._chat_ui.flush.call_count == 2, (
        f"flush 调用次数异常，期望 2 次，实际 {mock_loop._chat_ui.flush.call_count}"
    )


# ═══════════════════════════════════════════════════════════════════════════
#  测试：async_execute 异常路径处理
# ═══════════════════════════════════════════════════════════════════════════


class TestEditmsgPluginExceptionHandling:
    """测试 async_execute 在编辑过程中抛出异常时的行为

    验证三层防护中的 Layer 2：editmsg_plugin 的 except 子句
    恢复终端 + 给用户可见错误消息。
    """

    @pytest.mark.asyncio
    async def test_exception_writes_error_and_restores_terminal(
        self, EditmsgPlugin, mock_loop, mock_ctx, mock_session
    ):
        """编辑异常时：write_line 输出错误消息 + finally 恢复终端 + 返回 True"""
        plugin = EditmsgPlugin()
        plugin.bind_loop(mock_loop)

        with patch(
            "src.tui.pipeline.message_editor.edit_current_messages",
            side_effect=RuntimeError("terminal error"),
        ):
            result = await plugin.async_execute(mock_ctx)

        # ⑥ 返回 True：命令已被识别并处理（输出了错误提示）
        assert result is True

        # ① chat_ui.write_line 被调用 2 次（异常消息 + 取消提示）
        assert mock_loop._chat_ui.write_line.call_count == 2
        first_call_arg = mock_loop._chat_ui.write_line.call_args_list[0][0][0]
        assert "\u26a0" in first_call_arg  # ⚠ 字符
        assert "terminal error" in first_call_arg
        second_call_arg = mock_loop._chat_ui.write_line.call_args_list[1][0][0]
        assert "\u672a\u7f16\u8f91" in second_call_arg  # "未编辑"

        # ② monitor.start() 在 finally 中被调用（终端恢复）
        mock_loop._monitor.start.assert_called_once()

        # ③ chat_ui.resume() 在 finally 中被调用
        mock_loop._chat_ui.resume.assert_called_once()

        # ④ session.sync_retry_pending 未被调用（异常路径不执行编辑后逻辑）
        mock_session.sync_retry_pending.assert_not_called()
        mock_session.reset_retry_pending.assert_not_called()

        # ⑤ + ⑦ chat_ui.display_messages 未被调用（needs_rerender=False）
        mock_loop._chat_ui.display_messages.assert_not_called()
