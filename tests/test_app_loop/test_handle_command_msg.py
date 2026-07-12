"""Tests for InteractiveLoop._handle_command_msg — 命令分发逻辑。

验证纯 CommandPlugin 子类（无 loop/bind_loop/async_execute）不会因
AttributeError 崩溃，InteractiveCommandPlugin 子类仍能正常绑定 loop。
"""

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from src.app_loop._loop import InteractiveLoop
from src.core.commands.base import CommandPlugin, CommandMeta
from src.core.commands.plugins.base import InteractiveCommandPlugin
from src.core.internal.commands._command_core import CommandContext


# ═══════════════════════════════════════════════════════════
# Mock 插件类
# ═══════════════════════════════════════════════════════════

class MockPureCommandPlugin(CommandPlugin):
    """模拟纯 CommandPlugin 子类（无 loop/bind_loop/async_execute）。"""
    def __init__(self):
        self.meta = CommandMeta(name="test_pure", description="纯命令测试")

    def execute(self, ctx: CommandContext) -> bool:
        return True


class MockCommandPluginWithAsync(CommandPlugin):
    """模拟有 async_execute 但无 loop 的 CommandPlugin 子类（如 CompressCommand）。"""
    def __init__(self):
        self.meta = CommandMeta(name="test_async_cmd", description="异步命令测试")

    async def async_execute(self, ctx: CommandContext) -> bool:
        return True

    def execute(self, ctx: CommandContext) -> bool:
        return True


class MockInteractivePlugin(InteractiveCommandPlugin):
    """模拟 InteractiveCommandPlugin 子类。"""
    def __init__(self):
        super().__init__()
        self.meta = CommandMeta(name="test_interactive", description="交互式命令测试")

    def execute(self, ctx: CommandContext) -> bool:
        return True


class MockReturnFalsePlugin(CommandPlugin):
    """模拟返回 False 的纯 CommandPlugin 子类。"""
    def __init__(self):
        self.meta = CommandMeta(name="test_false", description="返回 False 测试")

    def execute(self, ctx: CommandContext) -> bool:
        return False


# ═══════════════════════════════════════════════════════════
# TestHandleCommandMsg
# ═══════════════════════════════════════════════════════════

class TestHandleCommandMsg:
    """测试 _handle_command_msg 的命令分发逻辑。"""

    @pytest.fixture
    def loop(self):
        """创建 InteractiveLoop 实例。"""
        l = InteractiveLoop()
        # mock _chat_ui 避免 TUI 依赖
        l._chat_ui = MagicMock()
        return l

    @pytest.fixture
    def session(self):
        """创建 mock session。"""
        session = MagicMock()
        session.messages = []
        session.agent.build_system_prompt = MagicMock(return_value=[])
        session.context_manager = None
        return session

    @pytest.fixture
    def state(self):
        """创建 mock state。"""
        from src.app_loop._session_setup import SessionState
        return SessionState(model="test-model")

    # ── 测试场景 1: 纯 CommandPlugin · 同步 execute ──────────

    @pytest.mark.asyncio
    async def test_pure_commandplugin_execute(self, loop, session, state):
        """纯 CommandPlugin（无 async_execute）→ 调用 execute"""
        plugin = MockPureCommandPlugin()

        with patch.object(loop, '_chat_ui') as mock_chat_ui:
            mock_chat_ui.bottom_bar = MagicMock()
            mock_chat_ui.wait_for_user_input = MagicMock(return_value="")

            with patch(
                'src.app_loop._loop.get_interactive_registry'
            ) as mock_registry_fn:
                mock_registry = MagicMock()
                mock_registry.get.return_value = plugin
                mock_registry_fn.return_value = mock_registry

                # 模拟 asyncio.to_thread 的行为 — 直接执行同步函数
                with patch('src.app_loop._loop.asyncio.to_thread',
                          new=AsyncMock(side_effect=lambda fn, ctx: fn(ctx))):
                    await loop._handle_command_msg("/test_pure", session, state)

        # 验证：纯 CommandPlugin 的 execute 被调用，没有崩溃
        assert state.model == "test-model"  # 未被修改（execute 返回 True 但 state_dict 未设置 model）

    # ── 测试场景 2: 有 async_execute 的 CommandPlugin ───────

    @pytest.mark.asyncio
    async def test_commandplugin_with_async_execute(self, loop, session, state):
        """CommandPlugin 有 async_execute → 调用 async_execute（不崩溃）"""
        plugin = MockCommandPluginWithAsync()

        with patch.object(loop, '_chat_ui') as mock_chat_ui:
            mock_chat_ui.bottom_bar = MagicMock()

            with patch(
                'src.app_loop._loop.get_interactive_registry'
            ) as mock_registry_fn:
                mock_registry = MagicMock()
                mock_registry.get.return_value = plugin
                mock_registry_fn.return_value = mock_registry

                await loop._handle_command_msg("/test_async_cmd", session, state)

        # 验证：能成功调用 async_execute，没有崩溃
        assert not loop._force_exit.is_set()

    # ── 测试场景 3: InteractiveCommandPlugin · 绑定 loop ───

    @pytest.mark.asyncio
    async def test_interactive_plugin_binds_loop(self, loop, session, state):
        """InteractiveCommandPlugin → 调用 bind_loop + async_execute"""
        plugin = MockInteractivePlugin()

        with patch.object(loop, '_chat_ui') as mock_chat_ui:
            mock_chat_ui.bottom_bar = MagicMock()
            mock_chat_ui.wait_for_user_input = MagicMock(return_value="")

            with patch(
                'src.app_loop._loop.get_interactive_registry'
            ) as mock_registry_fn:
                mock_registry = MagicMock()
                mock_registry.get.return_value = plugin
                mock_registry_fn.return_value = mock_registry

                await loop._handle_command_msg("/test_interactive", session, state)

        # 验证：loop 已绑定到插件
        assert plugin._loop is loop
        assert plugin.loop is loop

    # ── 测试场景 4: 插件不存在 ────────────────────────────

    @pytest.mark.asyncio
    async def test_plugin_not_found(self, loop, session, state):
        """registry.get() 返回 None → 输出未知命令"""
        with patch.object(loop, '_chat_ui') as mock_chat_ui:
            mock_chat_ui.bottom_bar = MagicMock()

            with patch(
                'src.app_loop._loop.get_interactive_registry'
            ) as mock_registry_fn:
                mock_registry = MagicMock()
                mock_registry.get.return_value = None
                mock_registry_fn.return_value = mock_registry

                await loop._handle_command_msg("/nonexistent", session, state)

        # 验证：输出未知命令
        mock_chat_ui.write_line.assert_called_once()
        args, _ = mock_chat_ui.write_line.call_args
        assert "未知命令" in args[0]

    # ── 测试场景 5: 插件返回 False ─────────────────────────

    @pytest.mark.asyncio
    async def test_plugin_returns_false(self, loop, session, state):
        """插件返回 False → 输出未知命令"""
        plugin = MockReturnFalsePlugin()

        with patch.object(loop, '_chat_ui') as mock_chat_ui:
            mock_chat_ui.bottom_bar = MagicMock()
            mock_chat_ui.wait_for_user_input = MagicMock(return_value="")

            with patch(
                'src.app_loop._loop.get_interactive_registry'
            ) as mock_registry_fn:
                mock_registry = MagicMock()
                mock_registry.get.return_value = plugin
                mock_registry_fn.return_value = mock_registry

                with patch('src.app_loop._loop.asyncio.to_thread',
                          new=AsyncMock(side_effect=lambda fn, ctx: fn(ctx))):
                    await loop._handle_command_msg("/test_false", session, state)

        # 验证：输出未知命令
        mock_chat_ui.write_line.assert_called_once()
        args, _ = mock_chat_ui.write_line.call_args
        assert "未知命令" in args[0]

    # ── 测试场景 6: 纯 CommandPlugin 不因无属性崩溃 ─────────

    @pytest.mark.asyncio
    async def test_pure_commandplugin_no_attribute_error(self, loop, session, state):
        """纯 CommandPlugin 不应因缺少 loop/bind_loop/async_execute 崩溃"""
        plugin = MockPureCommandPlugin()

        with patch.object(loop, '_chat_ui') as mock_chat_ui:
            mock_chat_ui.bottom_bar = MagicMock()
            mock_chat_ui.wait_for_user_input = MagicMock(return_value="")

            with patch(
                'src.app_loop._loop.get_interactive_registry'
            ) as mock_registry_fn:
                mock_registry = MagicMock()
                mock_registry.get.return_value = plugin
                mock_registry_fn.return_value = mock_registry

                with patch('src.app_loop._loop.asyncio.to_thread',
                          new=AsyncMock(side_effect=lambda fn, ctx: fn(ctx))):
                    # 确保不会抛出 AttributeError
                    try:
                        await loop._handle_command_msg("/test_pure", session, state)
                    except AttributeError:
                        pytest.fail("纯 CommandPlugin 抛出 AttributeError！")

        # 验证：正常完成，无异常
        assert not loop._force_exit.is_set()
