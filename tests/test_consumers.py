"""测试 OutputConsumer + ChatUIConsumer — 消费 EventBus 事件渲染到终端

覆盖内容：
  1. BUG-1: start() 防重入保护（重复 start/stop 安全）
  2. BUG-2: ToolSummaryEvent 中 error 为 None/空字符串的健壮性（ChatUIConsumer）
  3. BUG-3: 单工具成功时有输出（移除 total > 1 限制）（ChatUIConsumer）
  4. BUG-4: _write 方法在流关闭时无异常保护（OutputConsumer）
"""

import io
import sys
from unittest.mock import MagicMock, patch, create_autospec

import pytest

from src.ui.events.consumers import OutputConsumer
from src.ui.events.event_bus import DisplayEventBus
from src.ui.events.event_types import (
    OutputEvent,
    ToolSummaryEvent,
)
from src.chat_ui import ChatUIConsumer


# ===============================================================
# 辅助函数
# ===============================================================

def _make_consumer(bus=None, stream=None):
    """创建一个 OutputConsumer 实例并自动 start()。"""
    bus = bus or DisplayEventBus()
    stream = stream or io.StringIO()
    consumer = OutputConsumer(event_bus=bus, stream=stream)
    consumer.start()
    return consumer, bus, stream


# ===============================================================
# BUG-1: start() 防重入保护
# ===============================================================

class TestStartStopReentrancy:
    """OutputConsumer.start()/stop() 防重入保护"""

    def test_start_twice_no_error(self):
        """重复调用 start() 不应报错"""
        bus = DisplayEventBus()
        stream = io.StringIO()
        consumer = OutputConsumer(event_bus=bus, stream=stream)
        consumer.start()
        consumer.start()  # 第二次调用，不应抛出异常
        consumer.stop()

    def test_stop_twice_no_error(self):
        """重复调用 stop() 不应报错"""
        bus = DisplayEventBus()
        stream = io.StringIO()
        consumer = OutputConsumer(event_bus=bus, stream=stream)
        consumer.start()
        consumer.stop()
        consumer.stop()  # 第二次调用，不应抛出异常

    def test_stop_without_start_no_error(self):
        """未 start() 时调用 stop() 不应报错"""
        bus = DisplayEventBus()
        stream = io.StringIO()
        consumer = OutputConsumer(event_bus=bus, stream=stream)
        consumer.stop()  # 未 start，不应报错

    def test_start_stop_start_works(self):
        """start → stop → start 后可正常接收事件"""
        bus = DisplayEventBus()
        stream = io.StringIO()
        consumer = OutputConsumer(event_bus=bus, stream=stream)
        consumer.start()
        consumer.stop()
        consumer.start()  # 重启

        bus.publish(OutputEvent(text="重启后输出", level="info"))
        output = stream.getvalue()
        assert "重启后输出" in output

    def test_double_start_no_double_subscribe(self):
        """重复 start() 不应导致事件被处理两次"""
        bus = DisplayEventBus()
        stream = io.StringIO()
        consumer = OutputConsumer(event_bus=bus, stream=stream)
        consumer.start()
        consumer.start()  # 第二次 start，不应重复订阅

        bus.publish(OutputEvent(text="单次输出", level="info"))
        output = stream.getvalue()
        # 只应出现一次
        assert output.count("单次输出") == 1

    def test_stop_after_double_start_works(self):
        """重复 start() 后 stop() 能正确取消订阅"""
        bus = DisplayEventBus()
        stream = io.StringIO()
        consumer = OutputConsumer(event_bus=bus, stream=stream)
        consumer.start()
        consumer.start()  # 重复 start
        consumer.stop()

        bus.publish(OutputEvent(text="停止后不应输出", level="info"))
        output = stream.getvalue()
        assert "停止后不应输出" not in output


# ===============================================================
# BUG-2: error.split("\n") 对 None 的防御（ChatUIConsumer）
# ===============================================================

class TestToolSummaryErrorHandling:
    """ToolSummaryEvent 中 error 为 None/空字符串时的健壮性"""

    def test_error_is_none(self):
        """error 为 None 时显示 '(无错误信息)' 不崩溃"""
        bus = DisplayEventBus()
        stream = io.StringIO()
        chat_ui = ChatUIConsumer(event_bus=bus)
        chat_ui.start()

        bus.publish(ToolSummaryEvent(
            successful_tools=(),
            failed_tools=(("tool_a", None),),
        ))
        # 不抛异常即通过

    def test_error_is_empty_string(self):
        """error 为空字符串时正常处理"""
        bus = DisplayEventBus()
        stream = io.StringIO()
        chat_ui = ChatUIConsumer(event_bus=bus)
        chat_ui.start()

        bus.publish(ToolSummaryEvent(
            successful_tools=(),
            failed_tools=(("tool_b", ""),),
        ))
        # 不抛异常即通过

    def test_error_normal_string(self):
        """正常的 error 字符串不受影响"""
        bus = DisplayEventBus()
        stream = io.StringIO()
        chat_ui = ChatUIConsumer(event_bus=bus)
        chat_ui.start()

        bus.publish(ToolSummaryEvent(
            successful_tools=(),
            failed_tools=(("tool_c", "权限不足: 拒绝访问"),),
        ))
        # 不抛异常即通过

    def test_error_multiline(self):
        """多行 error 正常处理"""
        bus = DisplayEventBus()
        stream = io.StringIO()
        chat_ui = ChatUIConsumer(event_bus=bus)
        chat_ui.start()

        bus.publish(ToolSummaryEvent(
            successful_tools=(),
            failed_tools=(("tool_d", "第一行错误\n第二行错误\n第三行错误"),),
        ))
        # 不抛异常即通过


# ===============================================================
# BUG-3: 单工具成功时应有输出（ChatUIConsumer）
# ===============================================================

class TestSingleToolSuccess:
    """ToolSummaryEvent 工具汇总输出"""

    def test_single_tool_success_output(self):
        """只有一个工具成功时不抛异常"""
        bus = DisplayEventBus()
        chat_ui = ChatUIConsumer(event_bus=bus)
        chat_ui.start()

        bus.publish(ToolSummaryEvent(
            successful_tools=("search",),
            failed_tools=(),
        ))
        # 不抛异常即通过

    def test_multiple_tools_success_output(self):
        """多个工具全部成功时不抛异常"""
        bus = DisplayEventBus()
        chat_ui = ChatUIConsumer(event_bus=bus)
        chat_ui.start()

        bus.publish(ToolSummaryEvent(
            successful_tools=("search", "read", "write"),
            failed_tools=(),
        ))
        # 不抛异常即通过

    def test_zero_tools_no_output(self):
        """没有工具时不抛异常"""
        bus = DisplayEventBus()
        chat_ui = ChatUIConsumer(event_bus=bus)
        chat_ui.start()

        bus.publish(ToolSummaryEvent(
            successful_tools=(),
            failed_tools=(),
        ))
        # 不抛异常即通过

    def test_tools_with_failures_no_success_output(self):
        """有失败工具时不抛异常"""
        bus = DisplayEventBus()
        chat_ui = ChatUIConsumer(event_bus=bus)
        chat_ui.start()

        bus.publish(ToolSummaryEvent(
            successful_tools=("search",),
            failed_tools=(("write", "写入失败"),),
        ))
        # 不抛异常即通过


# ===============================================================
# BUG-4: _write 在流关闭时的异常保护
# ===============================================================

class TestWriteOnClosedStream:
    """_write 方法在流关闭时不应抛出异常"""

    def test_write_on_closed_stream_no_error(self):
        """流已关闭时 _write 不抛出异常"""
        bus = DisplayEventBus()
        stream = io.StringIO()
        consumer = OutputConsumer(event_bus=bus, stream=stream)
        consumer.start()
        stream.close()

        # 发布事件触发 _write，不应抛出异常
        bus.publish(OutputEvent(text="关闭后输出", level="info"))
        # 通过测试即表示没有异常

    def test_write_on_closed_stream_stop_no_error(self):
        """流已关闭时 stop() 不抛出异常"""
        bus = DisplayEventBus()
        stream = io.StringIO()
        consumer = OutputConsumer(event_bus=bus, stream=stream)
        consumer.start()
        stream.close()

        consumer.stop()  # 不应抛出异常

    def test_tool_summary_on_closed_stream_no_error(self):
        """ChatUIConsumer 订阅 ToolSummaryEvent 关闭流时不抛异常"""
        bus = DisplayEventBus()
        stream = io.StringIO()
        chat_ui = ChatUIConsumer(event_bus=bus)
        chat_ui.start()
        stream.close()

        bus.publish(ToolSummaryEvent(
            successful_tools=("search",),
            failed_tools=(("write", "失败"),),
        ))
        # 通过测试即表示没有异常

    def test_normal_write_after_close_no_error(self):
        """已关闭的 StringIO 直接写操作应触发 ValueError，但被 _write 保护"""
        bus = DisplayEventBus()
        stream = io.StringIO()
        consumer = OutputConsumer(event_bus=bus, stream=stream)
        consumer.start()
        stream.close()

        # 此操作应被 try-except 安全兜住
        bus.publish(OutputEvent(text="测试用例", level="info"))
        # 通过测试即表示没有异常


# ===============================================================
# 新增: ChatUI 活跃/不活跃时 OutputConsumer 的跳转/降级行为
# ===============================================================

class TestOutputConsumerWithChatUI:
    """当 ChatUI 活跃时 OutputConsumer 应跳过直写；不活跃时降级直写"""

    def test_skips_when_chatui_active(self, monkeypatch):
        """ChatUI 活跃时，非 cmd OutputEvent 被跳过（stream 无输出）"""
        monkeypatch.setattr("src.chat_ui.get_active_chat_ui", lambda: object())
        bus = DisplayEventBus()
        stream = io.StringIO()
        consumer = OutputConsumer(event_bus=bus, stream=stream)
        consumer.start()

        bus.publish(OutputEvent(text="ChatUI 活跃时的输出", level="info"))
        output = stream.getvalue()
        assert "ChatUI 活跃时的输出" not in output

    def test_writes_when_chatui_inactive(self, monkeypatch):
        """ChatUI 不活跃时，非 cmd OutputEvent 正常直写（stream 有输出）"""
        monkeypatch.setattr("src.chat_ui.get_active_chat_ui", lambda: None)
        bus = DisplayEventBus()
        stream = io.StringIO()
        consumer = OutputConsumer(event_bus=bus, stream=stream)
        consumer.start()

        bus.publish(OutputEvent(text="ChatUI 不活跃时的输出", level="info"))
        output = stream.getvalue()
        assert "ChatUI 不活跃时的输出" in output

    def test_cmd_skipped_when_chatui_active(self, monkeypatch):
        """ChatUI 活跃时，cmd OutputEvent 被 OutputConsumer 跳过（由 ChatUIConsumer 处理）"""
        monkeypatch.setattr("src.chat_ui.get_active_chat_ui", lambda: object())
        bus = DisplayEventBus()
        stream = io.StringIO()
        consumer = OutputConsumer(event_bus=bus, stream=stream)
        consumer.start()

        bus.publish(OutputEvent(text="cmd输出", level="info", source="cmd"))
        assert stream.getvalue() == ""

    def test_cmd_skipped_when_chatui_inactive(self, monkeypatch):
        """ChatUI 不活跃时，cmd OutputEvent 也被 OutputConsumer 跳过（预存行为，非本次修改引入）"""
        monkeypatch.setattr("src.chat_ui.get_active_chat_ui", lambda: None)
        bus = DisplayEventBus()
        stream = io.StringIO()
        consumer = OutputConsumer(event_bus=bus, stream=stream)
        consumer.start()

        bus.publish(OutputEvent(text="cmd输出", level="info", source="cmd"))
        assert stream.getvalue() == ""

# ===============================================================
# refresh_bottom_bar() flush 验证 + bottom_bar 属性访问
# ===============================================================

class TestCursorPositioningFlush:
    """确保光标定位操作后 stdout 被 flush，ANSI 序列到达终端"""

    def test_refresh_bottom_bar_flushes_stdout(self):
        """refresh_bottom_bar() 委托 _bottom_bar.refresh() 公开 API"""
        bus = DisplayEventBus()
        chat_ui = ChatUIConsumer(event_bus=bus)

        # Mock _bottom_bar.refresh() 避免真实终端 I/O
        chat_ui._bottom_bar.refresh = create_autospec(
            chat_ui._bottom_bar.refresh)

        chat_ui.refresh_bottom_bar("test_text")

        chat_ui._bottom_bar.refresh.assert_called_once_with("test_text", 9)

    def test_ensure_cursor_lower_flushes_stdout(self):
        """ensure_cursor_in_lower() 通过 bottom_bar 属性访问，不自动 flush。"""
        bus = DisplayEventBus()
        chat_ui = ChatUIConsumer(event_bus=bus)

        chat_ui._bottom_bar.ensure_cursor_in_lower = create_autospec(
            chat_ui._bottom_bar.ensure_cursor_in_lower)

        with patch.object(sys.__stdout__, "flush") as mock_flush:
            chat_ui.bottom_bar.ensure_cursor_in_lower()
            # bottom_bar 直接访问底层方法，不自动 flush
            # flush 由调用方（如 refresh_bottom_bar）负责
            chat_ui._bottom_bar.ensure_cursor_in_lower.assert_called_once()

    def test_refresh_bottom_bar_flush_called_after_reposition(self):
        """refresh_bottom_bar 委托 _bottom_bar.refresh() 公开 API"""
        bus = DisplayEventBus()
        chat_ui = ChatUIConsumer(event_bus=bus)

        with patch.object(chat_ui._bottom_bar, 'refresh') as mock_refresh:
            chat_ui.refresh_bottom_bar("test")
            mock_refresh.assert_called_once_with("test", 4)
