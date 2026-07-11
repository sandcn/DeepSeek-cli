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
        "src.ui.msg_list.edit_current_messages",
        new=lambda agent, state: state.update({"prefill": "old user content", "retry": False}),
    ):
        result = await plugin.async_execute(mock_ctx)

    assert result is True
    assert mock_ctx.state["prefill"] == "old user content"
    assert mock_ctx.state["retry"] is False
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
        "src.ui.msg_list.edit_current_messages",
        new=lambda agent, state: state.update({"prefill": "old content", "retry": True}),
    ):
        result = await plugin.async_execute(mock_ctx)

    assert result is True
    assert mock_ctx.state["prefill"] == "old content"
    assert mock_ctx.state["retry"] is True
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
        "src.ui.msg_list.edit_current_messages",
        new=lambda agent, state: state.update({"prefill": "", "retry": False}),
    ):
        result = await plugin.async_execute(mock_ctx)

    assert result is True
    assert mock_ctx.state["prefill"] == ""
    assert mock_ctx.state["retry"] is False
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
        "src.ui.msg_list.edit_current_messages",
        new=tracked_edit,
    ):
        result = await plugin.async_execute(mock_ctx)

    assert result is True
    # 调用顺序必须是: edit → sync_retry → reset_retry
    assert call_log == ["edit", "sync_retry", "reset_retry"], (
        f"调用顺序异常: {call_log}"
    )


@pytest.mark.asyncio
async def test_needs_rerender_with_prefill(EditmsgPlugin, mock_loop, mock_ctx, mock_session):
    """有 prefill 或 retry 时标记 needs_rerender，触发 display_messages"""
    plugin = EditmsgPlugin()
    plugin.bind_loop(mock_loop)

    with patch(
        "src.ui.msg_list.edit_current_messages",
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
        "src.ui.msg_list.edit_current_messages",
        new=lambda agent, state: state.update({"prefill": "", "retry": False}),
    ):
        await plugin.async_execute(mock_ctx)

    # chat_ui.display_messages 不应该被调用（needs_rerender=False）
    mock_loop._chat_ui.display_messages.assert_not_called()
