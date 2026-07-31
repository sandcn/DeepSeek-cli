"""test_display_target — OutputPublisher Protocol 与 get_output_publisher 工厂测试。

覆盖（方向④ publish_output Protocol 收敛 + P0 无头 None 语义修复）：
  1. OutputPublisher Protocol 结构化子类型校验（publish_output 满足协议）
  2. get_output_publisher 延迟导入路径正确（始终返回可调用的 publish_output）
  3. 无头模式链路保持（adapters/output.py write() 仍发布 OutputEvent，不静默丢弃）
  4. parallel_executor._publish_output 经工厂发布（None 降级防御性判断保留）
  5. write / write_with_lock / _publish_output 调用工厂而非直接 import
  6. Application.run finally 的 Goodbye!/致命错误输出链路回归（P0 修复）
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.core.adapters.output import DefaultOutputAdapter
from src.core.adapters.events import DisplayEventBusAdapter
from src.core.display_target import (
    OutputPublisher,
    get_output_publisher,
)
from src.core.parallel_executor import _publish_output


# ═══════════════════════════════════════════════════════════
# 1. OutputPublisher Protocol 结构化子类型校验
# ═══════════════════════════════════════════════════════════

class TestOutputPublisherProtocol:
    """publish_output 满足 OutputPublisher Protocol（结构化子类型）。"""

    def test_publish_output_satisfies_protocol(self) -> None:
        """src.tui.events.consumers.publish_output 可被 isinstance 识别。"""
        from src.tui.events.consumers import publish_output
        assert isinstance(publish_output, OutputPublisher)

    def test_re_export_publish_output_satisfies_protocol(self) -> None:
        """src.tui.events.publish_output re-export 同样满足协议。"""
        from src.tui.events import publish_output
        assert isinstance(publish_output, OutputPublisher)


# ═══════════════════════════════════════════════════════════
# 2. get_output_publisher 工厂（延迟导入 + 始终可调用）
# ═══════════════════════════════════════════════════════════

class TestGetOutputPublisher:
    """get_output_publisher 工厂语义。"""

    def test_headless_returns_publish_output(self) -> None:
        """无活跃 ChatUI（无头模式）→ 仍返回 publish_output（输出链路保持）。"""
        # P0 修复后工厂与 ChatUI 状态解耦，无需 patch get_active_chat_ui
        from src.tui.events import publish_output
        assert get_output_publisher() is publish_output

    def test_returns_publish_output_when_chatui_active(self) -> None:
        """TUI 模式（活跃 ChatUI）→ 返回 publish_output。"""
        with patch("src.tui.consumer.get_active_chat_ui", return_value=MagicMock()):
            from src.tui.events import publish_output
            publisher = get_output_publisher()
            assert publisher is publish_output

    def test_lazy_import_path(self) -> None:
        """工厂经 src.tui.events.publish_output 延迟导入获取发布函数。"""
        mock_pub = MagicMock(return_value=None)
        with patch("src.tui.events.publish_output", mock_pub):
            publisher = get_output_publisher()
            assert publisher is mock_pub


# ═══════════════════════════════════════════════════════════
# 3. adapters/output.py write / write_with_lock 改用工厂
# ═══════════════════════════════════════════════════════════

class TestOutputAdapterFactory:
    """DefaultOutputAdapter.write / write_with_lock 经工厂发布。"""

    def test_write_calls_factory(self) -> None:
        """write() 调用 get_output_publisher 并将参数透传给 publisher。"""
        mock_publisher = MagicMock()
        adapter = DefaultOutputAdapter()
        with patch("src.core.display_target.get_output_publisher",
                   return_value=mock_publisher):
            adapter.write("hello", level="info", source="core")
        mock_publisher.assert_called_once_with(
            "hello", level="info", source="core",
        )

    def test_write_with_lock_calls_factory(self) -> None:
        """write_with_lock() 调用 get_output_publisher 并透传参数。"""
        mock_publisher = MagicMock()
        adapter = DefaultOutputAdapter()
        with patch("src.core.display_target.get_output_publisher",
                   return_value=mock_publisher):
            adapter.write_with_lock("hello", level="raw", source="core")
        mock_publisher.assert_called_once_with(
            "hello", level="raw", source="core",
        )

    def test_write_none_publisher_noop(self) -> None:
        """工厂返回 None 时 write() 不抛异常（防御性判断保留，兼容未来 None 场景）。"""
        adapter = DefaultOutputAdapter()
        with patch("src.tui.events.consumers.DisplayEventBus") as mock_bus_cls, \
                patch("src.core.display_target.get_output_publisher",
                      return_value=None):
            adapter.write("hello")  # 防御性 no-op，不抛异常
        # 副作用断言：None 降级时不发布任何事件
        mock_bus_cls.get_default.return_value.publish.assert_not_called()

    def test_headless_write_still_publishes(self) -> None:
        """真实无头路径（get_active_chat_ui → None）：write() 输出不丢失（链路保持）。"""
        from src.tui.events.event_types import OutputEvent
        adapter = DefaultOutputAdapter()
        with patch("src.tui.events.consumers.DisplayEventBus") as mock_bus_cls:
            with patch("src.tui.consumer.get_active_chat_ui", return_value=None):
                adapter.write("hello")
            # 无头模式仍发布 OutputEvent（原链路：publish_output → EventBus
            # → OutputConsumer 无头时直写终端）
            mock_bus_cls.get_default.return_value.publish.assert_called_once()
            ev = mock_bus_cls.get_default.return_value.publish.call_args[0][0]
            assert isinstance(ev, OutputEvent)
            assert ev.text == "hello"
            assert ev.level == "info"
            assert ev.source == "core"

    def test_write_with_lock_none_publisher_noop(self) -> None:
        """工厂返回 None 时 write_with_lock() 不抛异常（防御性判断保留）。"""
        adapter = DefaultOutputAdapter()
        with patch("src.tui.events.consumers.DisplayEventBus") as mock_bus_cls, \
                patch("src.core.display_target.get_output_publisher",
                      return_value=None):
            adapter.write_with_lock("hello")  # 防御性 no-op，不抛异常
        # 副作用断言：None 降级时不发布任何事件
        mock_bus_cls.get_default.return_value.publish.assert_not_called()


# ═══════════════════════════════════════════════════════════
# 4. adapters/events.py _publish_output 改用工厂
# ═══════════════════════════════════════════════════════════

class TestEventsAdapterFactory:
    """DisplayEventBusAdapter._publish_output 经工厂发布。"""

    def test_publish_output_calls_factory(self) -> None:
        """_publish_output() 调用工厂并透传参数（source 默认 core）。"""
        mock_publisher = MagicMock()
        adapter = DisplayEventBusAdapter()
        with patch("src.core.display_target.get_output_publisher",
                   return_value=mock_publisher):
            adapter._publish_output("hello", level="warning", source="core")
        mock_publisher.assert_called_once_with(
            "hello", level="warning", source="core",
        )

    def test_publish_output_none_publisher_noop(self) -> None:
        """工厂返回 None 时 _publish_output() 不抛异常（防御性判断保留）。"""
        adapter = DisplayEventBusAdapter()
        with patch("src.tui.events.consumers.DisplayEventBus") as mock_bus_cls, \
                patch("src.core.display_target.get_output_publisher",
                      return_value=None):
            adapter._publish_output("hello")
        # 副作用断言：None 降级时不发布任何事件
        mock_bus_cls.get_default.return_value.publish.assert_not_called()


# ═══════════════════════════════════════════════════════════
# 5. parallel_executor._publish_output 模块级 helper
# ═══════════════════════════════════════════════════════════

class TestParallelExecutorPublish:
    """parallel_executor 模块级 _publish_output helper 经工厂发布。"""

    def test_parallel_publish_calls_factory(self) -> None:
        """_publish_output() 调用工厂并透传参数。"""
        mock_publisher = MagicMock()
        with patch("src.core.display_target.get_output_publisher",
                   return_value=mock_publisher):
            _publish_output("\r", level="raw")
        mock_publisher.assert_called_once_with(
            "\r", level="raw", source="",
        )

    def test_parallel_publish_none_noop(self) -> None:
        """工厂返回 None 时 _publish_output() 不抛异常（防御性判断保留）。"""
        with patch("src.tui.events.consumers.DisplayEventBus") as mock_bus_cls, \
                patch("src.core.display_target.get_output_publisher",
                      return_value=None):
            _publish_output("", level="raw")
        # 副作用断言：None 降级时不发布任何事件
        mock_bus_cls.get_default.return_value.publish.assert_not_called()


# ═══════════════════════════════════════════════════════════
# 6. Application.run finally 输出链路回归（P0 修复）
# ═══════════════════════════════════════════════════════════

class TestFatalAndGoodbyeChain:
    """Application.run finally 的 output_port.write 输出不丢失（P0 回归）。

    模拟 src/application.py run() finally 路径：output_port.write 在
    无头/有头任何模式下都必须发布 OutputEvent（原链路保留），
    不得因 get_output_publisher 返回 None 而静默丢弃。
    """

    def test_goodbye_output_published(self) -> None:
        """output_port.write("  Goodbye!", level="raw") → OutputEvent 被发布。"""
        from src.tui.events.event_types import OutputEvent
        with patch("src.tui.events.consumers.DisplayEventBus") as mock_bus_cls:
            DefaultOutputAdapter().write("  Goodbye!", level="raw")
            mock_bus_cls.get_default.return_value.publish.assert_called_once()
            ev = mock_bus_cls.get_default.return_value.publish.call_args[0][0]
            assert isinstance(ev, OutputEvent)
            assert ev.text == "  Goodbye!"
            assert ev.level == "raw"

    def test_fatal_error_output_published(self) -> None:
        """致命错误输出（level="error"）→ OutputEvent 被发布。"""
        from src.tui.events.event_types import OutputEvent
        with patch("src.tui.events.consumers.DisplayEventBus") as mock_bus_cls:
            DefaultOutputAdapter().write("\n  ❌ 致命错误: boom", level="error")
            mock_bus_cls.get_default.return_value.publish.assert_called_once()
            ev = mock_bus_cls.get_default.return_value.publish.call_args[0][0]
            assert isinstance(ev, OutputEvent)
            assert ev.text == "\n  ❌ 致命错误: boom"
            assert ev.level == "error"
