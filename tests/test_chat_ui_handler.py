"""ChatUIErrorHandler 与 RenderCommand.ERROR 单元测试

测试范围：
1. ChatUIErrorHandler.emit() 正常格式化并分发 ERROR record
2. emit 时 ChatUI 未启动（get_active_chat_ui() 返回 None）静默跳过
3. 仅处理 ERROR+ 级别，WARNING/INFO/DEBUG 跳过
4. 跳过 _chatui_reported 标记的 record（防自引用）
5. RenderCommand.ERROR 枚举值和分发表条目正确

测试隔离：每个 test 移除并重新注册 _error_handler，
避免全局 handler 在测试间互相污染。
"""

import logging
from unittest.mock import patch, MagicMock

import pytest

from src import chat_ui


# ── Fixture: ChatUIErrorHandler 实例 ───────────────────

@pytest.fixture(autouse=True)
def _reset_error_handler():
    """每个测试前后从 root logger 移除/恢复 _error_handler。

    chat_ui 模块级导入时自动注册了 _error_handler 到 root logger。
    为隔离测试，每次测试前移除，测试后恢复。
    若 handler 不存在（模块未加载），跳过。
    """
    root = logging.getLogger()
    from src.chat_ui.error_handler import ChatUIErrorHandler
    handler = None
    for h in root.handlers:
        if isinstance(h, ChatUIErrorHandler):
            handler = h
            break
    if handler is not None and handler in root.handlers:
        root.removeHandler(handler)
    yield
    if handler is not None and handler not in root.handlers:
        root.addHandler(handler)


# ── Test: 正常分发 ─────────────────────────────────────

class TestChatUIErrorHandlerEmit:
    """ChatUIErrorHandler.emit() 核心分发逻辑测试"""

    def test_emit_error_record_dispatches_to_on_error(self):
        """emit ERROR record → 调用 get_active_chat_ui().on_error()"""
        handler = chat_ui.ChatUIErrorHandler()
        record = logging.LogRecord(
            name="test.module", level=logging.ERROR,
            pathname="", lineno=0, msg="something went wrong",
            args=(), exc_info=None,
        )

        mock_ui = MagicMock()
        with patch.object(chat_ui.state, 'get_active_chat_ui', return_value=mock_ui):
            handler.emit(record)

        mock_ui.on_error.assert_called_once()
        # 消息格式应为 "test.module: something went wrong"
        call_msg = mock_ui.on_error.call_args[0][0]
        assert "test.module" in call_msg
        assert "something went wrong" in call_msg

    def test_emit_critical_record_dispatches_to_on_error(self):
        """emit CRITICAL record → 同样调用 on_error()"""
        handler = chat_ui.ChatUIErrorHandler()
        record = logging.LogRecord(
            name="app", level=logging.CRITICAL,
            pathname="", lineno=0, msg="fatal error",
            args=(), exc_info=None,
        )

        mock_ui = MagicMock()
        with patch.object(chat_ui.state, 'get_active_chat_ui', return_value=mock_ui):
            handler.emit(record)

        mock_ui.on_error.assert_called_once()
        assert "fatal error" in mock_ui.on_error.call_args[0][0]

    def test_emit_empty_message_skipped(self):
        """空消息 record（msg=""）→ 不调用 on_error()"""
        handler = chat_ui.ChatUIErrorHandler()
        record = logging.LogRecord(
            name="test", level=logging.ERROR,
            pathname="", lineno=0, msg="",
            args=(), exc_info=None,
        )

        mock_ui = MagicMock()
        with patch.object(chat_ui.state, 'get_active_chat_ui', return_value=mock_ui):
            handler.emit(record)

        mock_ui.on_error.assert_not_called()

    def test_emit_long_message_truncated(self):
        """超长消息（>200 字符）→ 截断并追加"..." """
        handler = chat_ui.ChatUIErrorHandler(max_length=50)
        long_msg = "x" * 100
        record = logging.LogRecord(
            name="m", level=logging.ERROR,
            pathname="", lineno=0, msg=long_msg,
            args=(), exc_info=None,
        )

        mock_ui = MagicMock()
        with patch.object(chat_ui.state, 'get_active_chat_ui', return_value=mock_ui):
            handler.emit(record)

        called_msg = mock_ui.on_error.call_args[0][0]
        assert len(called_msg) <= 53  # 50 + "..."
        assert called_msg.endswith("...")


# ── Test: ChatUI 未激活跳过 ────────────────────────────

class TestChatUIErrorHandlerInactive:
    """get_active_chat_ui() 返回 None 时 handler 静默跳过"""

    def test_emit_when_no_active_consumer(self):
        """ChatUI 未启动 → emit 不崩溃，不调用 on_error"""
        handler = chat_ui.ChatUIErrorHandler()
        record = logging.LogRecord(
            name="test", level=logging.ERROR,
            pathname="", lineno=0, msg="error when no UI",
            args=(), exc_info=None,
        )

        mock_ui = MagicMock()
        with patch.object(chat_ui.state, 'get_active_chat_ui', return_value=None):
            # 不应抛出任何异常
            handler.emit(record)

        mock_ui.on_error.assert_not_called()


# ── Test: 级别过滤 ────────────────────────────────────

class TestChatUIErrorHandlerLevelFilter:
    """仅 ERROR+ 级别通过，低级别跳过"""

    @pytest.mark.parametrize("level,levelno,should_emit", [
        ("WARNING", logging.WARNING, False),
        ("INFO",    logging.INFO,    False),
        ("DEBUG",   logging.DEBUG,   False),
        ("ERROR",   logging.ERROR,   True),
        ("CRITICAL", logging.CRITICAL, True),
    ])
    def test_level_filtering(self, level, levelno, should_emit):
        handler = chat_ui.ChatUIErrorHandler()
        record = logging.LogRecord(
            name="test", level=levelno,
            pathname="", lineno=0, msg=f"test {level}",
            args=(), exc_info=None,
        )

        mock_ui = MagicMock()
        with patch.object(chat_ui.state, 'get_active_chat_ui', return_value=mock_ui):
            handler.emit(record)

        if should_emit:
            mock_ui.on_error.assert_called_once()
        else:
            mock_ui.on_error.assert_not_called()


# ── Test: 防自引用循环 ────────────────────────────────

class TestChatUIErrorHandlerSelfRef:
    """_chatui_reported 标记防止自引用循环"""

    def test_emits_stops_on_reported_flag(self):
        """已标记 _chatui_reported 的 record → emit 跳过"""
        handler = chat_ui.ChatUIErrorHandler()
        record = logging.LogRecord(
            name="test", level=logging.ERROR,
            pathname="", lineno=0, msg="already reported",
            args=(), exc_info=None,
        )
        # 模拟已在之前的 emit 调用中设置了标记
        record._chatui_reported = True

        mock_ui = MagicMock()
        with patch.object(chat_ui.state, 'get_active_chat_ui', return_value=mock_ui):
            handler.emit(record)

        mock_ui.on_error.assert_not_called()

    def test_emit_sets_reported_flag(self):
        """emit 正常处理后 record 被标记 _chatui_reported=True"""
        handler = chat_ui.ChatUIErrorHandler()
        record = logging.LogRecord(
            name="test", level=logging.ERROR,
            pathname="", lineno=0, msg="normal error",
            args=(), exc_info=None,
        )

        mock_ui = MagicMock()
        with patch.object(chat_ui.state, 'get_active_chat_ui', return_value=mock_ui):
            handler.emit(record)

        # emit 后 record 必须被标记
        assert getattr(record, '_chatui_reported', False) is True

    def test_no_recursion_via_reentrant_guard(self):
        """同一线程 emit → on_error → logger → emit → 线程重入标记阻断递归

        验证 thread-local _handler_reentrant.is_active 能阻断
        on_error 路径中意外产生的二次 emit。
        """
        from src.chat_ui.error_handler import _handler_reentrant
        # 确保测试开始时重入标记为 False
        _handler_reentrant.is_active = False

        handler = chat_ui.ChatUIErrorHandler()

        call_count = 0

        def _on_error_side_effect(msg):
            """模拟 on_error 路径中意外触发 logger.error"""
            nonlocal call_count
            call_count += 1
            # 再次创建 record 并调用 emit（模拟 on_error 路径中的日志）
            inner_record = logging.LogRecord(
                name="test", level=logging.ERROR,
                pathname="", lineno=0, msg="inner error (from on_error path)",
                args=(), exc_info=None,
            )
            # 此时 handler 已处于 emit 中（reentrant.is_active=True）
            # 该次 emit 应被重入标记阻断，不调用 on_error
            handler.emit(inner_record)

        mock_ui = MagicMock()
        mock_ui.on_error.side_effect = _on_error_side_effect

        with patch.object(chat_ui.state, 'get_active_chat_ui', return_value=mock_ui):
            handler.emit(
                logging.LogRecord(
                    name="test", level=logging.ERROR,
                    pathname="", lineno=0, msg="outer error",
                    args=(), exc_info=None,
                )
            )

        # on_error 只被调用 1 次（outer record 触发），inner 被重入标记阻断
        assert call_count == 1
        # 退出 emit 后重入标记已清除
        assert getattr(_handler_reentrant, 'is_active', False) is False


# ── Test: RenderCommand.ERROR 枚举 ─────────────────────

class TestRenderCommandError:
    """RenderCommand.ERROR 枚举值和分发表"""

    def test_error_enum_value(self):
        """ERROR = 16"""
        from src.chat_ui import RenderCommand
        assert RenderCommand.ERROR == 16
        assert isinstance(RenderCommand.ERROR, RenderCommand)

    def test_error_in_dispatch(self):
        """_RENDER_DISPATCH 包含 ERROR 条目"""
        from src.chat_ui.renderer import _RENDER_DISPATCH
        dispatch = _RENDER_DISPATCH
        assert 16 in dispatch
        method_name, arg_indices = dispatch[16]
        assert method_name == "_do_error"
        assert arg_indices == (1,)

    def test_error_instance_creation(self):
        """ERROR 枚举值可实例化命令元组"""
        from src.chat_ui import RenderCommand
        cmd = (RenderCommand.ERROR, "test error message")
        assert cmd[0] == 16
        assert cmd[1] == "test error message"


# ── Test: _on_model_phase handler ─────────────────────

class TestOnModelPhase:
    """_on_model_phase handler 过滤与分发逻辑测试"""

    @pytest.fixture
    def consumer(self):
        """返回一个 ChatUIConsumer 实例，cmd_queue 清空。"""
        from src.chat_ui import ChatUIConsumer
        c = ChatUIConsumer()
        # 清空可能残留的消息
        while not c._engine._cmd_queue.empty():
            c._engine._cmd_queue.get_nowait()
        return c

    def test_error_phase_dispatches_error(self, consumer):
        """phase="error" + label="_MAIN_LABEL" + 非空 info → push ERROR 命令"""
        from src.chat_ui import _MAIN_LABEL, RenderCommand
        from src.ui.events.event_types import ModelPhaseEvent
        event = ModelPhaseEvent(
            label=_MAIN_LABEL, phase="error", info="test timeout error",
        )
        consumer._disp._on_model_phase(event)
        cmd = consumer._engine._cmd_queue.get_nowait()
        assert cmd[0] == RenderCommand.ERROR
        assert "test timeout error" in cmd[1]

    def test_subagent_label_skipped(self, consumer):
        """SubAgent label（!= _MAIN_LABEL）→ 不 push 任何命令"""
        from src.chat_ui import RenderCommand
        from src.ui.events.event_types import ModelPhaseEvent
        event = ModelPhaseEvent(
            label="subagent", phase="error", info="subagent error",
        )
        consumer._disp._on_model_phase(event)
        assert consumer._engine._cmd_queue.empty()

    def test_non_error_phase_skipped(self, consumer):
        """非 error phase（如 "thinking"）→ 不 push 任何命令"""
        from src.chat_ui import _MAIN_LABEL
        from src.ui.events.event_types import ModelPhaseEvent
        event = ModelPhaseEvent(
            label=_MAIN_LABEL, phase="thinking", info="thinking...",
        )
        consumer._disp._on_model_phase(event)
        assert consumer._engine._cmd_queue.empty()

    def test_empty_info_skipped(self, consumer):
        """空 info → 不 push 任何命令"""
        from src.chat_ui import _MAIN_LABEL
        from src.ui.events.event_types import ModelPhaseEvent
        event = ModelPhaseEvent(
            label=_MAIN_LABEL, phase="error", info="",
        )
        consumer._disp._on_model_phase(event)
        assert consumer._engine._cmd_queue.empty()

    def test_long_info_truncated(self, consumer):
        """超长 info（>200 字符）→ 截断并追加"..." """
        from src.chat_ui import _MAIN_LABEL, RenderCommand
        from src.ui.events.event_types import ModelPhaseEvent
        long_info = "x" * 300
        event = ModelPhaseEvent(
            label=_MAIN_LABEL, phase="error", info=long_info,
        )
        consumer._disp._on_model_phase(event)
        cmd = consumer._engine._cmd_queue.get_nowait()
        assert cmd[0] == RenderCommand.ERROR
        result = cmd[1]
        assert len(result) == 203  # 200 + "..."
        assert result.endswith("...")
        # 截断后的内容应为前 200 个字符 + "..."
        assert result == "x" * 200 + "..."

    def test_short_info_not_truncated(self, consumer):
        """短 info（<=200 字符）→ 原样传递"""
        from src.chat_ui import _MAIN_LABEL, RenderCommand
        from src.ui.events.event_types import ModelPhaseEvent
        info = "short error message"
        event = ModelPhaseEvent(
            label=_MAIN_LABEL, phase="error", info=info,
        )
        consumer._disp._on_model_phase(event)
        cmd = consumer._engine._cmd_queue.get_nowait()
        assert cmd[0] == RenderCommand.ERROR
        assert cmd[1] == info


# ═══════════════════════════════════════════════════════
# _is_agent_source None 保护测试（步骤 9）
# ═══════════════════════════════════════════════════════

class TestIsAgentSource:
    """EventDispatcher._is_agent_source None 保护测试"""

    def test_none_source_returns_false(self):
        """source=None → 返回 False，不抛异常"""
        from src.chat_ui.dispatcher import EventDispatcher
        assert EventDispatcher._is_agent_source(None) is False

    def test_main_source_returns_true(self):
        """source='agent' → 返回 True"""
        from src.chat_ui.dispatcher import EventDispatcher
        from src.chat_ui.const import _MAIN_SOURCE
        assert EventDispatcher._is_agent_source(_MAIN_SOURCE) is True

    def test_agent_prefix_returns_true(self):
        """source='agent-1' → 返回 True"""
        from src.chat_ui.dispatcher import EventDispatcher
        assert EventDispatcher._is_agent_source("agent-1") is True

    def test_other_source_returns_false(self):
        """source='user' → 返回 False"""
        from src.chat_ui.dispatcher import EventDispatcher
        assert EventDispatcher._is_agent_source("user") is False

    def test_empty_string_returns_false(self):
        """source='' → 返回 False"""
        from src.chat_ui.dispatcher import EventDispatcher
        assert EventDispatcher._is_agent_source("") is False


# ═══════════════════════════════════════════════════════
# emit() Type­Error 保护测试（步骤 6）
# ═══════════════════════════════════════════════════════

class TestEmitGetMessageTypeError:
    """record.getMessage() 格式化参数不匹配时的 TypeError 保护"""

    def test_get_message_type_error_skipped(self):
        """getMessage() 抛出 TypeError → emit 静默跳过，不崩溃"""
        import logging
        from unittest.mock import MagicMock, patch
        from src import chat_ui

        handler = chat_ui.ChatUIErrorHandler()

        # 构造格式化参数不匹配的 LogRecord：格式字符串需要 2 个参数但只给了 1 个
        record = logging.LogRecord(
            name="test", level=logging.ERROR,
            pathname="", lineno=0, msg="format mismatch %s %s",
            args=("only_one",), exc_info=None,
        )

        mock_ui = MagicMock()
        with patch.object(chat_ui.state, 'get_active_chat_ui', return_value=mock_ui):
            # 不应抛出任何异常
            handler.emit(record)

        # 因 getMessage() 失败，on_error 不应被调用
        mock_ui.on_error.assert_not_called()


# ═══════════════════════════════════════════════════════
# stop() unsubscribe 保护测试（步骤 7）
# ═══════════════════════════════════════════════════════

class TestStopUnsubscribeSafe:
    """ChatUIConsumer.stop() 中 unsubscribe 异常保护"""

    def test_stop_unsubscribe_safe(self):
        """unsubscribe 抛出异常 → stop() 不传播异常"""
        from unittest.mock import MagicMock, patch
        from src.chat_ui import ChatUIConsumer

        consumer = ChatUIConsumer()

        # mock EventBus.unsubscribe 抛出异常
        consumer._bus.unsubscribe = MagicMock(
            side_effect=Exception("unsubscribe failed"),
        )

        # 模拟已启动状态（否则 stop 提前返回）
        consumer._started = True
        consumer._bound_handlers = {type: "handler"}

        # stop() 不应传播 unsubscribe 的异常
        try:
            consumer.stop()
            # 成功到达这里即可（异常被捕获）
            passed = True
        except Exception:
            passed = False

        assert passed, "stop() 中 unsubscribe 异常应被捕获"


# ═══════════════════════════════════════════════════════
# wait_for_user_input 空字符串处理测试（步骤 11）
# ═══════════════════════════════════════════════════════

class TestWaitForUserInput:
    """wait_for_user_input 区分 None 和空字符串"""

    def test_empty_string_returns_immediately(self):
        """get_queued_input() 返回 "" → 直接返回空字符串（不再继续等待）"""
        from unittest.mock import MagicMock
        from src.chat_ui import ChatUIConsumer

        consumer = ChatUIConsumer()

        # 模拟 monitor：仅返回 ""（空字符串是有效输入）
        mock_monitor = MagicMock()
        mock_monitor.get_queued_input.side_effect = [""]

        result = consumer.wait_for_user_input(mock_monitor, timeout=10)

        # 空字符串 "" 是用户按 Enter 的有效输入，应直接返回
        assert result == ""
        # get_queued_input 应仅被调用 1 次（第 1 次返回 "" 即退出）
        assert mock_monitor.get_queued_input.call_count == 1

    def test_none_keeps_waiting(self):
        """get_queued_input() 返回 None → 继续等待"""
        from unittest.mock import MagicMock
        from src.chat_ui import ChatUIConsumer

        consumer = ChatUIConsumer()

        # 模拟 monitor：先返回 None，再返回 "text"
        mock_monitor = MagicMock()
        mock_monitor.get_queued_input.side_effect = [None, "text"]

        result = consumer.wait_for_user_input(mock_monitor, timeout=10)

        assert result == "text"

    def test_timeout_returns_empty(self):
        """超时 → 返回空字符串"""
        from unittest.mock import MagicMock
        from src.chat_ui import ChatUIConsumer

        consumer = ChatUIConsumer()

        mock_monitor = MagicMock()
        mock_monitor.get_queued_input.return_value = None

        result = consumer.wait_for_user_input(mock_monitor, timeout=0.1)

        assert result == ""
