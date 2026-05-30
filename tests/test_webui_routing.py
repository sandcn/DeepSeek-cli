"""测试 src/webui/routing/handlers.py 中的 WebSocket 消息处理函数。

asyncio_mode = auto 已在 pytest.ini 配置，测试函数可直接使用 async def。
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.webui._pending_selects import pending_selects
from src.webui.routing.context import ConnectionContext
from src.webui.routing.handlers import (
    WS_MESSAGE_HANDLERS,
    _handle_delete_session,
    _handle_sandbox_get_files,
    _handle_sandbox_file_diff,
    _handle_get_messages_req,
    _handle_edit_messages,
    _handle_get_full_state,
    _handle_get_models,
    _handle_get_sessions,
    _handle_load_session,
    _handle_ping,
    _handle_rename_session,
    _handle_set_model,
    _handle_stop_generating,
    _handle_user_message,
    _handle_user_select,
)


# ═══════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def mock_ctx():
    """创建 mock ConnectionContext 对象。"""
    ctx = MagicMock(spec=ConnectionContext)
    ctx.session = MagicMock()
    ctx.session.model = "deepseek-v4-flash"
    ctx.session.messages = []
    ctx.session.session_id = "test-session-id"
    ctx.ws_send = AsyncMock()
    ctx.my_select_ids = set()
    ctx.msg_idx_state = MagicMock()
    ctx.message_queue = MagicMock()
    ctx.message_queue.put = AsyncMock()
    ctx.proc_state = MagicMock()
    ctx.proc_state.current_task = None
    import asyncio
    _task_ready = asyncio.Event()
    _task_ready.set()  # 预先设置，避免 wait_for 超时等待 2 秒
    ctx.proc_state.task_ready = _task_ready
    return ctx


@pytest.fixture
def mock_models():
    """模拟 MODELS 列表。"""
    return ["deepseek-v4-flash", "gpt-4", "claude-3"]


# ═══════════════════════════════════════════════════════════════
# _handle_user_message
# ═══════════════════════════════════════════════════════════════

class TestHandleUserMessage:
    """_handle_user_message: 用户消息入队与 exit 命令处理。"""

    async def test_normal_message_puts_into_queue(self, mock_ctx):
        """正常消息应放入 msg_queue。"""
        data = {"content": "hello world"}
        await _handle_user_message(data, mock_ctx)
        mock_ctx.message_queue.put.assert_awaited_once_with("hello world")

    async def test_empty_content_skips_queue(self, mock_ctx):
        """空 content 应跳过，不放入队列。"""
        data = {"content": ""}
        await _handle_user_message(data, mock_ctx)
        mock_ctx.message_queue.put.assert_not_awaited()

    async def test_missing_content_skips_queue(self, mock_ctx):
        """缺少 content 键应跳过，不放入队列。"""
        data = {}
        await _handle_user_message(data, mock_ctx)
        mock_ctx.message_queue.put.assert_not_awaited()

    async def test_whitespace_only_skips_queue(self, mock_ctx):
        """纯空白 content 应跳过，不放入队列。"""
        data = {"content": "   \n\t  "}
        await _handle_user_message(data, mock_ctx)
        mock_ctx.message_queue.put.assert_not_awaited()

    async def test_exit_message_triggers_exit_command(self, mock_ctx):
        """"exit" 消息应触发 _handle_exit_command。"""
        data = {"content": "exit"}
        with patch(
            "src.webui.routing.handlers._handle_exit_command",
            AsyncMock(),
        ) as mock_exit:
            await _handle_user_message(data, mock_ctx)
            mock_exit.assert_awaited_once_with(mock_ctx)
        mock_ctx.message_queue.put.assert_not_awaited()

    async def test_exit_message_case_insensitive(self, mock_ctx):
        """"EXIT" 大写也应触发 exit 流程。"""
        data = {"content": "EXIT"}
        with patch(
            "src.webui.routing.handlers._handle_exit_command",
            AsyncMock(),
        ) as mock_exit:
            await _handle_user_message(data, mock_ctx)
            mock_exit.assert_awaited_once_with(mock_ctx)

    async def test_exit_with_whitespace(self, mock_ctx):
        """"  exit  " 带空白也应识别为 exit。"""
        data = {"content": "  exit  "}
        with patch(
            "src.webui.routing.handlers._handle_exit_command",
            AsyncMock(),
        ) as mock_exit:
            await _handle_user_message(data, mock_ctx)
            mock_exit.assert_awaited_once_with(mock_ctx)


# ═══════════════════════════════════════════════════════════════
# _handle_user_select
# ═══════════════════════════════════════════════════════════════

class TestHandleUserSelect:
    """_handle_user_select: 用户选择完成 Future。"""

    async def test_select_id_in_pending_future_completed(self, mock_ctx):
        """select_id 在 PENDING_SELECTS 中 → Future 应被正确完成。"""
        future = MagicMock()
        future.done.return_value = False
        select_id = "sel_001"
        mock_ctx.my_select_ids.add(select_id)

        data = {
            "select_id": select_id,
            "action": "confirmed",
            "selected": ["opt_a", "opt_b"],
        }
        with patch.object(pending_selects, '_pending',
            {select_id: future},
        ):
            await _handle_user_select(data, mock_ctx)

        future.set_result.assert_called_once()
        call_arg = json.loads(future.set_result.call_args[0][0])
        assert call_arg["action"] == "confirmed"
        assert call_arg["selected"] == ["opt_a", "opt_b"]

    async def test_select_id_not_in_pending_skipped(self, mock_ctx):
        """select_id 不在 PENDING_SELECTS 中 → 跳过。"""
        future = MagicMock()

        data = {
            "select_id": "sel_unknown",
            "action": "confirmed",
            "selected": [],
        }
        with patch.object(pending_selects, '_pending',
            {"sel_other": future},
        ):
            await _handle_user_select(data, mock_ctx)

        future.set_result.assert_not_called()

    async def test_select_id_removed_from_my_select_ids(self, mock_ctx):
        """select_id 应从 my_select_ids 中移除。"""
        future = MagicMock()
        future.done.return_value = False
        select_id = "sel_002"
        mock_ctx.my_select_ids.add(select_id)
        mock_ctx.my_select_ids.add("sel_other")

        data = {
            "select_id": select_id,
            "action": "confirmed",
            "selected": [],
        }
        with patch.object(pending_selects, '_pending',
            {select_id: future},
        ):
            await _handle_user_select(data, mock_ctx)

        assert select_id not in mock_ctx.my_select_ids
        assert "sel_other" in mock_ctx.my_select_ids

    async def test_already_done_future_not_set_again(self, mock_ctx):
        """Future 已 done 时不应再 set_result。"""
        future = MagicMock()
        future.done.return_value = True
        select_id = "sel_done"
        mock_ctx.my_select_ids.add(select_id)

        data = {
            "select_id": select_id,
            "action": "confirmed",
            "selected": [],
        }
        with patch.object(pending_selects, '_pending',
            {select_id: future},
        ):
            await _handle_user_select(data, mock_ctx)

        future.set_result.assert_not_called()

    async def test_empty_select_id_handled_gracefully(self, mock_ctx):
        """空的 select_id 应被安全处理。"""
        future = MagicMock()

        data = {
            "select_id": "",
            "action": "confirmed",
            "selected": [],
        }
        with patch.object(pending_selects, '_pending',
            {"": future},
        ):
            await _handle_user_select(data, mock_ctx)

        # 空字符串在 PENDING_SELECTS 中匹配时，应完成 Future
        assert "" not in mock_ctx.my_select_ids

    async def test_missing_select_id_handled(self, mock_ctx):
        """缺失 select_id 键时应安全处理。"""
        data = {"action": "confirmed", "selected": []}
        with patch.object(pending_selects, '_pending',
            {},
        ):
            # 不应抛出异常
            await _handle_user_select(data, mock_ctx)


# ═══════════════════════════════════════════════════════════════
# _handle_stop_generating
# ═══════════════════════════════════════════════════════════════

class TestHandleStopGenerating:
    """_handle_stop_generating: 停止生成。"""

    async def test_calls_request_interrupt_async(self, mock_ctx):
        """应调用 request_interrupt_async。"""
        with patch(
            "src.webui.routing.handlers.request_interrupt_async",
        ) as mock_interrupt:
            await _handle_stop_generating({}, mock_ctx)
            mock_interrupt.assert_called_once()

    async def test_cancels_current_task_when_exists(self, mock_ctx):
        """有 current_task 时应取消。"""
        mock_task = MagicMock()
        mock_task.done.return_value = False
        mock_ctx.proc_state.current_task = mock_task

        with patch(
            "src.webui.routing.handlers.request_interrupt_async",
        ):
            await _handle_stop_generating({}, mock_ctx)
            mock_task.cancel.assert_called_once()

    async def test_no_current_task_does_not_fail(self, mock_ctx):
        """无 current_task 时不应失败。"""
        mock_ctx.proc_state.current_task = None

        with patch(
            "src.webui.routing.handlers.request_interrupt_async",
        ):
            # 不应抛出异常
            await _handle_stop_generating({}, mock_ctx)


# ═══════════════════════════════════════════════════════════════
# _handle_get_models
# ═══════════════════════════════════════════════════════════════

class TestHandleGetModels:
    """_handle_get_models: 返回模型列表。"""

    async def test_returns_models_list(self, mock_ctx, mock_models):
        """应返回正确的 models_list 消息。"""
        with patch(
            "src.webui.routing.handlers.MODELS",
            mock_models,
        ):
            await _handle_get_models({}, mock_ctx)

        mock_ctx.ws_send.assert_awaited_once()
        sent = mock_ctx.ws_send.call_args[0][0]
        assert sent["type"] == "models_list"
        assert sent["models"] == mock_models
        assert sent["current"] == mock_ctx.session.model


# ═══════════════════════════════════════════════════════════════
# _handle_set_model
# ═══════════════════════════════════════════════════════════════

class TestHandleSetModel:
    """_handle_set_model: 切换模型。"""

    async def test_valid_model_updates_session(self, mock_ctx, mock_models):
        """有效模型应更新 session.model 并发送 model_changed。"""
        data = {"model": "gpt-4"}

        with patch(
            "src.webui.routing.handlers.MODELS",
            mock_models,
        ):
            await _handle_set_model(data, mock_ctx)

        assert mock_ctx.session.model == "gpt-4"
        mock_ctx.session.save.assert_called_once()
        mock_ctx.ws_send.assert_awaited_once()
        sent = mock_ctx.ws_send.call_args[0][0]
        assert sent["type"] == "model_changed"
        assert sent["model"] == "gpt-4"

    async def test_invalid_model_sends_warning(self, mock_ctx, mock_models):
        """无效模型应发送 command_output warning。"""
        data = {"model": "nonexistent-model"}

        with patch(
            "src.webui.routing.handlers.MODELS",
            mock_models,
        ):
            await _handle_set_model(data, mock_ctx)

        mock_ctx.ws_send.assert_awaited_once()
        sent = mock_ctx.ws_send.call_args[0][0]
        assert sent["type"] == "command_output"
        assert sent["level"] == "warning"
        assert "nonexistent-model" in sent["text"]
        # 模型未被修改
        assert mock_ctx.session.model == "deepseek-v4-flash"

    async def test_empty_model_skipped(self, mock_ctx, mock_models):
        """空模型名应跳过，不发送任何消息。"""
        data = {"model": ""}

        with patch(
            "src.webui.routing.handlers.MODELS",
            mock_models,
        ):
            await _handle_set_model(data, mock_ctx)

        mock_ctx.ws_send.assert_not_awaited()
        assert mock_ctx.session.model == "deepseek-v4-flash"


# ═══════════════════════════════════════════════════════════════
# _handle_get_sessions
# ═══════════════════════════════════════════════════════════════

class TestHandleGetSessions:
    """_handle_get_sessions: 获取会话列表。"""

    async def test_returns_sessions_list_with_current_id(self, mock_ctx):
        """应返回 sessions_list 并携带 current_id。"""
        mock_sessions = [
            {"id": "s1", "title": "会话1"},
            {"id": "s2", "title": "会话2"},
        ]
        mock_ctx.session.list_sessions.return_value = mock_sessions
        mock_ctx.session.session_id = "s1"

        with patch(
            "src.webui.routing.handlers.msg_sessions_list",
            return_value={
                "type": "sessions_list",
                "sessions": mock_sessions,
                "current_id": "s1",
            },
        ):
            await _handle_get_sessions({}, mock_ctx)

        mock_ctx.ws_send.assert_awaited_once()
        sent = mock_ctx.ws_send.call_args[0][0]
        assert sent["type"] == "sessions_list"
        assert sent["sessions"] == mock_sessions
        assert sent["current_id"] == "s1"

    async def test_empty_session_id(self, mock_ctx):
        """current_id 为空字符串时也应正确返回。"""
        mock_ctx.session.list_sessions.return_value = []
        mock_ctx.session.session_id = ""

        with patch(
            "src.webui.routing.handlers.msg_sessions_list",
            return_value={
                "type": "sessions_list",
                "sessions": [],
                "current_id": "",
            },
        ):
            await _handle_get_sessions({}, mock_ctx)

        mock_ctx.ws_send.assert_awaited_once()


# ═══════════════════════════════════════════════════════════════
# _handle_delete_session
# ═══════════════════════════════════════════════════════════════

class TestHandleDeleteSession:
    """_handle_delete_session: 删除会话。"""

    async def test_successful_deletion_sends_session_deleted(self, mock_ctx):
        """成功删除应发送 session_deleted。"""
        data = {"session_id": "s1"}

        with patch(
            "src.webui.routing.handlers._delete_session",
            return_value=True,
        ), patch(
            "src.webui.routing.handlers.msg_session_deleted",
            return_value={"type": "session_deleted", "session_id": "s1"},
        ):
            await _handle_delete_session(data, mock_ctx)

        mock_ctx.ws_send.assert_awaited_once()
        sent = mock_ctx.ws_send.call_args[0][0]
        assert sent["type"] == "session_deleted"
        assert sent["session_id"] == "s1"

    async def test_failed_deletion_sends_error(self, mock_ctx):
        """删除失败应发送 command_output error。"""
        data = {"session_id": "s1"}

        with patch(
            "src.webui.routing.handlers._delete_session",
            return_value=False,
        ):
            await _handle_delete_session(data, mock_ctx)

        mock_ctx.ws_send.assert_awaited_once()
        sent = mock_ctx.ws_send.call_args[0][0]
        assert sent["type"] == "command_output"
        assert sent["level"] == "error"
        assert "s1" in sent["text"]

    async def test_empty_session_id_skipped(self, mock_ctx):
        """空的 session_id 应跳过。"""
        data = {"session_id": ""}
        await _handle_delete_session(data, mock_ctx)
        mock_ctx.ws_send.assert_not_awaited()

    async def test_missing_session_id_skipped(self, mock_ctx):
        """缺失 session_id 键应跳过。"""
        data = {}
        await _handle_delete_session(data, mock_ctx)
        mock_ctx.ws_send.assert_not_awaited()


# ═══════════════════════════════════════════════════════════════
# _handle_load_session
# ═══════════════════════════════════════════════════════════════

class TestHandleLoadSession:
    """_handle_load_session: 加载历史会话。"""

    async def test_empty_session_id_skipped(self, mock_ctx):
        """空的 session_id 应跳过。"""
        data = {"session_id": ""}
        await _handle_load_session(data, mock_ctx)
        mock_ctx.ws_send.assert_not_awaited()

    async def test_missing_session_id_skipped(self, mock_ctx):
        """缺失 session_id 键应跳过。"""
        data = {}
        await _handle_load_session(data, mock_ctx)
        mock_ctx.ws_send.assert_not_awaited()

    async def test_session_not_found_sends_error(self, mock_ctx):
        """会话不存在应发送错误消息。"""
        mock_ctx.session.messages = []
        mock_ctx.session.load.return_value = None
        data = {"session_id": "nonexistent"}

        await _handle_load_session(data, mock_ctx)

        mock_ctx.ws_send.assert_awaited_once()
        sent = mock_ctx.ws_send.call_args[0][0]
        assert sent["type"] == "command_output"
        assert sent["level"] == "error"
        assert "nonexistent" in sent["text"]

    async def test_session_exists_sends_loaded_and_model_changed(self, mock_ctx):
        """会话存在应发送 session_loaded 和 model_changed。"""
        mock_ctx.session.messages = []
        mock_ctx.session.load.return_value = {"id": "s1", "messages": []}
        mock_ctx.session.model = "gpt-4"
        data = {"session_id": "s1"}

        rebuild_result = [{"index": 0, "role": "user", "content": "hi"}]

        with patch(
            "src.webui.routing.handlers._rebuild_message_indices",
            return_value=rebuild_result,
        ), patch(
            "src.webui.routing.handlers.msg_session_loaded",
            return_value={
                "type": "session_loaded",
                "session_id": "s1",
                "model": "gpt-4",
                "messages": rebuild_result,
            },
        ):
            await _handle_load_session(data, mock_ctx)

        assert mock_ctx.ws_send.await_count == 3
        first = mock_ctx.ws_send.call_args_list[0][0][0]
        second = mock_ctx.ws_send.call_args_list[1][0][0]
        third = mock_ctx.ws_send.call_args_list[2][0][0]
        assert first["type"] == "session_loaded"
        assert first["session_id"] == "s1"
        assert second["type"] == "model_changed"
        assert second["model"] == "gpt-4"
        assert third["type"] == "sandbox_updated"

    async def test_session_with_existing_messages_saves_first(self, mock_ctx):
        """有非 system 消息时应先保存当前会话。"""
        mock_ctx.session.messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]
        mock_ctx.session.session_id = "current-session"
        mock_ctx.session.load.return_value = {"id": "s1", "messages": []}
        mock_ctx.session.model = "gpt-4"
        data = {"session_id": "s1"}

        with patch(
            "src.webui.routing.handlers._rebuild_message_indices",
            return_value=[],
        ), patch(
            "src.webui.routing.handlers.msg_session_loaded",
            return_value={
                "type": "session_loaded",
                "session_id": "s1",
                "model": "gpt-4",
                "messages": [],
            },
        ):
            await _handle_load_session(data, mock_ctx)

        # save 应在 load 之前被调用
        mock_ctx.session.save.assert_called()
        mock_ctx.session.load.assert_called_with("s1")

    async def test_session_without_current_id_saves_without_id(self, mock_ctx):
        """当前 session_id 为空时，save 时 session_id 应为 None。"""
        mock_ctx.session.messages = [
            {"role": "user", "content": "hello"},
        ]
        mock_ctx.session.session_id = None
        mock_ctx.session.load.return_value = {"id": "s1", "messages": []}
        mock_ctx.session.model = "gpt-4"
        data = {"session_id": "s1"}

        with patch(
            "src.webui.routing.handlers._rebuild_message_indices",
            return_value=[],
        ), patch(
            "src.webui.routing.handlers.msg_session_loaded",
            return_value={
                "type": "session_loaded",
                "session_id": "s1",
                "model": "gpt-4",
                "messages": [],
            },
        ):
            await _handle_load_session(data, mock_ctx)

        mock_ctx.session.save.assert_called()
        mock_ctx.session.load.assert_called_with("s1")


# ═══════════════════════════════════════════════════════════════
# _handle_get_full_state
# ═══════════════════════════════════════════════════════════════

class TestHandleGetFullState:
    """_handle_get_full_state: 返回完整会话状态。"""

    async def test_returns_full_state(self, mock_ctx):
        """应返回包含 rebuilt messages 和 model 的完整状态。"""
        mock_ctx.session.model = "deepseek-v4-flash"
        rebuilt = [
            {"index": 0, "role": "user", "content": "hi"},
            {"index": 1, "role": "assistant", "content": "hello"},
        ]

        with patch(
            "src.webui.routing.handlers._rebuild_message_indices",
            return_value=rebuilt,
        ):
            await _handle_get_full_state({}, mock_ctx)

        mock_ctx.ws_send.assert_awaited_once()
        sent = mock_ctx.ws_send.call_args[0][0]
        assert sent["type"] == "full_state"
        assert sent["messages"] == rebuilt
        assert sent["model"] == "deepseek-v4-flash"

    async def test_returns_empty_messages_when_none(self, mock_ctx):
        """无消息时应返回空列表。"""
        mock_ctx.session.model = "gpt-4"

        with patch(
            "src.webui.routing.handlers._rebuild_message_indices",
            return_value=[],
        ):
            await _handle_get_full_state({}, mock_ctx)

        mock_ctx.ws_send.assert_awaited_once()
        sent = mock_ctx.ws_send.call_args[0][0]
        assert sent["type"] == "full_state"
        assert sent["messages"] == []
        assert sent["model"] == "gpt-4"


# ═══════════════════════════════════════════════════════════════
# WS_MESSAGE_HANDLERS 路由表
# ═══════════════════════════════════════════════════════════════

class TestWSMessageHandlers:
    """WS_MESSAGE_HANDLERS 路由表验证。"""

    def test_all_15_routes_exist(self):
        """路由表应包含全部 15 个路由映射。"""
        assert len(WS_MESSAGE_HANDLERS) == 15, (
            f"预期 15 个路由，实际 {len(WS_MESSAGE_HANDLERS)} 个"
        )

    def test_routes_map_to_correct_handlers(self):
        """每个路由应映射到对应的 handler 函数。"""
        # 预期映射：消息类型 → handler 函数
        expected = {
            "user_message": _handle_user_message,
            "user_select": _handle_user_select,
            "stop_generating": _handle_stop_generating,
            "get_sandbox_files": _handle_sandbox_get_files,
            "get_sandbox_file_diff": _handle_sandbox_file_diff,
            "get_messages": _handle_get_messages_req,
            "edit_messages_action": _handle_edit_messages,
            "get_full_state": _handle_get_full_state,
            "get_models": _handle_get_models,
            "set_model": _handle_set_model,
            "get_sessions": _handle_get_sessions,
            "delete_session": _handle_delete_session,
            "load_session": _handle_load_session,
            "rename_session": _handle_rename_session,
            "ping": _handle_ping,
        }
        for msg_type, expected_handler in expected.items():
            assert msg_type in WS_MESSAGE_HANDLERS, (
                f"路由表中缺少消息类型: {msg_type}"
            )
            assert WS_MESSAGE_HANDLERS[msg_type] is expected_handler, (
                f"消息类型 '{msg_type}' 映射的 handler 不匹配: "
                f"预期 {expected_handler.__name__ if hasattr(expected_handler, '__name__') else expected_handler}, "
                f"实际 {WS_MESSAGE_HANDLERS[msg_type].__name__}"
            )

    def test_all_routes_are_callable(self):
        """所有路由值应为可调用函数。"""
        for msg_type, handler in WS_MESSAGE_HANDLERS.items():
            assert callable(handler), (
                f"消息类型 '{msg_type}' 的 handler 不可调用: {handler}"
            )

    def test_no_unexpected_routes(self):
        """不应有多余的意外路由。"""
        expected_keys = {
            "user_message", "user_select", "stop_generating",
            "get_sandbox_files", "get_sandbox_file_diff",
            "get_messages", "edit_messages_action",
            "get_full_state", "get_models", "set_model",
            "get_sessions", "delete_session", "load_session",
            "rename_session", "ping",
        }
        actual_keys = set(WS_MESSAGE_HANDLERS.keys())
        unexpected = actual_keys - expected_keys
        assert not unexpected, f"存在意外路由: {unexpected}"
        missing = expected_keys - actual_keys
        assert not missing, f"缺少路由: {missing}"


# ═══════════════════════════════════════════════════════════════
# _handle_rename_session
# ═══════════════════════════════════════════════════════════════

class TestHandleRenameSession:
    """_handle_rename_session: 重命名会话标题。"""

    async def test_rename_success(self, mock_ctx):
        """正常重命名应发送 session_renamed。"""
        mock_ctx.session.session_id = "test-session-id"
        data = {"title": "新标题"}
        with patch(
            "src.webui.routing.handlers._rename_session",
            return_value=True,
        ):
            await _handle_rename_session(data, mock_ctx)
        mock_ctx.ws_send.assert_awaited_once()
        sent = mock_ctx.ws_send.call_args[0][0]
        assert sent["type"] == "session_renamed"
        assert sent["session_id"] == "test-session-id"
        assert sent["title"] == "新标题"

    async def test_rename_failure(self, mock_ctx):
        """重命名失败应发送 command_output error。"""
        mock_ctx.session.session_id = "test-session-id"
        data = {"title": "新标题"}
        with patch(
            "src.webui.routing.handlers._rename_session",
            return_value=False,
        ):
            await _handle_rename_session(data, mock_ctx)
        mock_ctx.ws_send.assert_awaited_once()
        sent = mock_ctx.ws_send.call_args[0][0]
        assert sent["type"] == "command_output"
        assert sent["level"] == "error"

    async def test_empty_title_skipped(self, mock_ctx):
        """空标题应跳过。"""
        data = {"title": ""}
        await _handle_rename_session(data, mock_ctx)
        mock_ctx.ws_send.assert_not_awaited()

    async def test_whitespace_title_skipped(self, mock_ctx):
        """纯空白标题应跳过。"""
        data = {"title": "   "}
        await _handle_rename_session(data, mock_ctx)
        mock_ctx.ws_send.assert_not_awaited()

    async def test_no_session_id_sends_warning(self, mock_ctx):
        """没有 session_id 时应发送 warning。"""
        mock_ctx.session.session_id = None
        data = {"title": "新标题"}
        await _handle_rename_session(data, mock_ctx)
        mock_ctx.ws_send.assert_awaited_once()
        sent = mock_ctx.ws_send.call_args[0][0]
        assert sent["type"] == "command_output"
        assert sent["level"] == "warning"

    async def test_missing_title_key_skipped(self, mock_ctx):
        """缺失 title 键应跳过。"""
        data = {}
        await _handle_rename_session(data, mock_ctx)
        mock_ctx.ws_send.assert_not_awaited()
