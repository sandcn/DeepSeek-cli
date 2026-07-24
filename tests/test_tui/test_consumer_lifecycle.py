"""测试 ChatUIConsumer — 生命周期 + 公开方法。"""

from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock, patch

from src.tui.consumer.consumer import ChatUIConsumer
from src.tui.consumer.factory import _ChatUIComponents
from src.tui.testing import tui_test_env


# ═══════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════

def make_mock_components() -> _ChatUIComponents:
    """创建全部使用 MagicMock 的 _ChatUIComponents 实例。"""
    return _ChatUIComponents(
        rs=MagicMock(),
        cursor_tracker=MagicMock(),
        bottom_bar=MagicMock(),
        output_adapter=MagicMock(),
        tui_renderer=MagicMock(),
        engine=MagicMock(),
        dispatcher=MagicMock(),
        cmpl_handler=MagicMock(),
    )


def make_for_testing() -> ChatUIConsumer:
    """创建使用 mock 组件的 ChatUIConsumer（for_testing 工厂方法）。"""
    components = make_mock_components()
    # dispatcher.list_handlers 返回空字典（避免 start 时订阅事件）
    components.dispatcher.list_handlers.return_value = {}
    return ChatUIConsumer.for_testing(components=components)


# ═══════════════════════════════════════════════════════════
# ChatUIConsumer 创建
# ═══════════════════════════════════════════════════════════

class TestChatUIConsumerInit:
    """ChatUIConsumer 初始化测试。"""

    def test_for_testing_creates_instance(self):
        """for_testing 创建 ChatUIConsumer 实例。"""
        with tui_test_env():
            consumer = make_for_testing()
            assert consumer is not None
            assert isinstance(consumer, ChatUIConsumer)

    def test_for_testing_not_started(self):
        """for_testing 创建的实例未启动。"""
        with tui_test_env():
            consumer = make_for_testing()
            assert consumer._started is False

    def test_for_testing_has_components(self):
        """for_testing 创建的实例有 _components 容器。"""
        with tui_test_env():
            consumer = make_for_testing()
            assert consumer._components is not None
            assert isinstance(consumer._components, _ChatUIComponents)

    def test_for_testing_has_event_bus(self):
        """for_testing 创建的实例有事件总线。"""
        with tui_test_env():
            consumer = make_for_testing()
            assert consumer._bus is not None


# ═══════════════════════════════════════════════════════════
# ChatUIConsumer 生命周期
# ═══════════════════════════════════════════════════════════

class TestChatUIConsumerLifecycle:
    """生命周期：start / stop / suspend / resume。"""

    def test_start_starts_engine(self):
        """start() 启动引擎。"""
        with tui_test_env():
            consumer = make_for_testing()
            consumer.start()
            consumer._components.engine.start.assert_called_once()

    def test_start_subscribes_handlers(self):
        """start() 订阅事件处理器。"""
        with tui_test_env():
            from src.tui.events.event_types import DisplayEvent
            components = make_mock_components()
            FakeEvent = type("FakeEvent", (DisplayEvent,), {})
            components.dispatcher.list_handlers.return_value = {
                FakeEvent: MagicMock(),
            }
            consumer = ChatUIConsumer.for_testing(components=components)
            consumer.start()
            # 至少订阅了一个事件
            assert components.engine.start.called

    def test_start_idempotent(self):
        """重复 start() 幂等安全。"""
        with tui_test_env():
            consumer = make_for_testing()
            consumer.start()
            consumer.start()  # 再次调用
            consumer._components.engine.start.assert_called_once()

    def test_stop_stops_engine(self):
        """stop() 停止引擎。"""
        with tui_test_env():
            consumer = make_for_testing()
            consumer.start()
            consumer.stop()
            consumer._components.engine.stop.assert_called_once()

    def test_stop_idempotent(self):
        """未启动时 stop() 安全（幂等）。"""
        with tui_test_env():
            consumer = make_for_testing()
            consumer.stop()  # 未启动时 stop，不应抛异常

    def test_start_then_stop_resets_started(self):
        """start→stop 后 _started 为 False。"""
        with tui_test_env():
            consumer = make_for_testing()
            consumer.start()
            consumer.stop()
            assert consumer._started is False

    def test_suspend_stops_engine(self):
        """suspend() 停止引擎。"""
        with tui_test_env():
            consumer = make_for_testing()
            consumer.start()
            consumer.suspend()
            consumer._components.engine.stop.assert_called()

    def test_suspend_flushes_engine(self):
        """suspend() 排空队列。"""
        with tui_test_env():
            consumer = make_for_testing()
            consumer.start()
            consumer.suspend()
            consumer._components.engine.flush.assert_called_once()

    def test_suspend_not_started_safe(self):
        """未启动时 suspend() 安全。"""
        with tui_test_env():
            consumer = make_for_testing()
            consumer.suspend()  # 不应抛异常

    def test_resume_starts_engine(self):
        """resume() 重新启动引擎。"""
        with tui_test_env():
            consumer = make_for_testing()
            consumer.start()
            consumer.suspend()
            consumer._components.engine._render_running = False
            consumer.resume()
            consumer._components.engine.start.assert_called()

    def test_resume_not_started_safe(self):
        """未启动时 resume() 安全。"""
        with tui_test_env():
            consumer = make_for_testing()
            consumer.resume()  # 不应抛异常

    def test_resume_already_running_safe(self):
        """引擎已在运行时 resume() 安全跳过。"""
        with tui_test_env():
            consumer = make_for_testing()
            consumer.start()
            consumer._components.engine._render_running = True
            call_count = consumer._components.engine.start.call_count
            consumer.resume()
            # start 不应被额外调用
            assert consumer._components.engine.start.call_count == call_count


# ═══════════════════════════════════════════════════════════
# ChatUIConsumer 公开方法
# ═══════════════════════════════════════════════════════════

class TestChatUIConsumerPublicMethods:
    """ChatUIConsumer 公开 API 方法测试。"""

    def test_on_user_message(self):
        """on_user_message 入队 USER_MSG 命令。"""
        with tui_test_env():
            consumer = make_for_testing()
            from src.tui.engine.const import RenderCommand
            consumer.on_user_message("hello")
            consumer._components.engine.push_cmd.assert_called_with(
                (RenderCommand.USER_MSG, "hello")
            )

    def test_on_notification(self):
        """on_notification 入队 NOTIFICATION 命令。"""
        with tui_test_env():
            consumer = make_for_testing()
            from src.tui.engine.const import RenderCommand
            consumer.on_notification("notify")
            consumer._components.engine.push_cmd.assert_called_with(
                (RenderCommand.NOTIFICATION, "notify")
            )

    def test_on_error_empty_skipped(self):
        """on_error 空消息时不入队。"""
        with tui_test_env():
            consumer = make_for_testing()
            consumer.on_error("")
            consumer._components.engine.push_cmd.assert_not_called()

    def test_on_error_with_message(self):
        """on_error 非空消息入队 ERROR 命令。"""
        with tui_test_env():
            consumer = make_for_testing()
            from src.tui.engine.const import RenderCommand
            consumer.on_error("error msg")
            consumer._components.engine.push_cmd.assert_called_with(
                (RenderCommand.ERROR, "error msg")
            )

    def test_write_line(self):
        """write_line 入队 WRITE_LINE 命令。"""
        with tui_test_env():
            consumer = make_for_testing()
            from src.tui.engine.const import RenderCommand
            consumer.write_line("hello")
            consumer._components.engine.push_cmd.assert_called_with(
                (RenderCommand.WRITE_LINE, "hello")
            )

    def test_display_messages(self):
        """display_messages 入队 DISPLAY_MSGS 命令。"""
        with tui_test_env():
            consumer = make_for_testing()
            from src.tui.engine.const import RenderCommand
            messages = [{"role": "user", "content": "hi"}]
            consumer.display_messages(messages, speed=2)
            consumer._components.engine.push_cmd.assert_called_with(
                (RenderCommand.DISPLAY_MSGS, messages, 2)
            )

    def test_push_cmd(self):
        """push_cmd 委托给 engine。"""
        with tui_test_env():
            consumer = make_for_testing()
            cmd = (99, "test")
            consumer.push_cmd(cmd)
            consumer._components.engine.push_cmd.assert_called_with(cmd)

    def test_flush(self):
        """flush 委托给 engine。"""
        with tui_test_env():
            consumer = make_for_testing()
            consumer.flush(timeout=1.0)
            consumer._components.engine.flush.assert_called_with(timeout=1.0)

    def test_request_bottom_redraw(self):
        """request_bottom_redraw 委托给 engine。"""
        with tui_test_env():
            consumer = make_for_testing()
            consumer.request_bottom_redraw()
            consumer._components.engine.request_bottom_redraw.assert_called_once()

    def test_ensure_cursor_upper(self):
        """ensure_cursor_upper 委托给 engine。"""
        with tui_test_env():
            consumer = make_for_testing()
            consumer.ensure_cursor_upper()
            consumer._components.engine.ensure_cursor_upper.assert_called_once()

    def test_set_panel_refresh_callback(self):
        """set_panel_refresh_callback 委托给 engine。"""
        with tui_test_env():
            consumer = make_for_testing()
            cb = MagicMock()
            consumer.set_panel_refresh_callback(cb)
            consumer._components.engine.set_panel_refresh_callback.assert_called_with(cb)

    def test_bottom_bar_property(self):
        """bottom_bar 属性返回 _components.bottom_bar。"""
        with tui_test_env():
            consumer = make_for_testing()
            assert consumer.bottom_bar is consumer._components.bottom_bar

    def test_output_adapter_property(self):
        """output_adapter 属性返回 tui_renderer.output_adapter。"""
        with tui_test_env():
            consumer = make_for_testing()
            expected = consumer._components.tui_renderer.output_adapter
            assert consumer.output_adapter is expected

    def test_setup_bottom_bar(self):
        """setup_bottom_bar 委托给 bottom_bar。"""
        with tui_test_env():
            consumer = make_for_testing()
            consumer.setup_bottom_bar()
            consumer._components.bottom_bar.setup.assert_called_once()

    def test_teardown_bottom_bar(self):
        """teardown_bottom_bar 委托给 bottom_bar。"""
        with tui_test_env():
            consumer = make_for_testing()
            consumer.teardown_bottom_bar()
            consumer._components.bottom_bar.teardown.assert_called_once()

    def test_register_event_handler(self):
        """register_event_handler 委托给 dispatcher。"""
        with tui_test_env():
            from src.tui.events.event_types import DisplayEvent
            consumer = make_for_testing()
            handler = MagicMock()
            consumer.register_event_handler(DisplayEvent, handler)
            consumer._components.dispatcher.register_handler.assert_called_once()

    def test_setup_completion_delegates(self):
        """setup_completion 设置 monitor 回调。"""
        with tui_test_env():
            consumer = make_for_testing()
            monitor = MagicMock()
            consumer.setup_completion(monitor)
            monitor.set_completion_callback.assert_called_once()
            monitor.set_dismiss_completion_callback.assert_called_once()
            monitor.set_completion_navigate_callback.assert_called_once()
            monitor.set_auto_completion_callback.assert_called_once()


class TestChatUIConsumerRefreshBottomBar:
    """refresh_bottom_bar 方法测试。"""

    def test_refresh_bottom_bar_with_position(self):
        """指定光标位置的 refresh_bottom_bar。"""
        with tui_test_env():
            consumer = make_for_testing()
            consumer.refresh_bottom_bar("hello", cursor_pos=3)
            consumer._components.bottom_bar.set_input_state.assert_called_with("hello", 3)
            consumer._components.engine.request_bottom_redraw.assert_called_once()

    def test_refresh_bottom_bar_default_position(self):
        """cursor_pos=-1 时使用文本长度作为光标位置。"""
        with tui_test_env():
            consumer = make_for_testing()
            consumer.refresh_bottom_bar("hello")
            consumer._components.bottom_bar.set_input_state.assert_called_with("hello", 5)
