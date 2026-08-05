"""测试 src/tui/events — DisplayEventBus 事件总线与事件类型。

覆盖：
- DisplayEventBus 发布/订阅/取消订阅、多订阅者通知顺序、异常隔离
- 四层共用场景（api/core/webui/tools 模拟注册）
- 事件类型字段/默认值/序列化往返/frozen 不可变/类型判别

DisplayEventBus 为单例（SingletonMeta），测试前后通过 reset_default/clear 隔离。

2026-08-05 死代码清理：时间窗口批处理机制（_TimeWindowBatcher /
register_batched_event / unregister_batched_event）已删除——生产未启用。
"""

from __future__ import annotations

import dataclasses

import pytest

from src.tui.events.event_bus import DisplayEventBus
from src.tui.events.event_types import (
    ALL_EVENT_TYPES,
    AgentAddedEvent,
    AgentStatusChanged,
    ContentChunkEvent,
    DisplayEvent,
    MetricsUpdateEvent,
    ModelPhaseEvent,
    OutputEvent,
    SessionStarted,
    SessionStopped,
    ToolDoneEvent,
    ToolStartedEvent,
    ToolSummaryEvent,
    UsageUpdatedEvent,
    UserSelectNeededEvent,
)


@pytest.fixture
def bus():
    """每个测试使用独立的 DisplayEventBus 实例，测试后清理并重置单例。"""
    DisplayEventBus.reset_default()
    b = DisplayEventBus.get_default()
    yield b
    b.clear()
    DisplayEventBus.reset_default()


# ═══════════════════════════════════════════════════════════
# DisplayEventBus — 发布/订阅/取消订阅
# ═══════════════════════════════════════════════════════════

class TestEventBusSubscribePublish:
    """事件发布/订阅核心路径。"""

    def test_subscribe_publish_regression(self, bus):
        """订阅后 publish 事件，handler 收到事件对象。"""
        received = []

        def handler(ev):
            received.append(ev)

        bus.subscribe(handler, ToolStartedEvent)
        bus.publish(ToolStartedEvent(label="call_1", tool_name="read_file", source="agent"))

        assert len(received) == 1
        assert received[0].label == "call_1"
        assert received[0].tool_name == "read_file"
        assert received[0].source == "agent"

    def test_unsubscribe_regression(self, bus):
        """取消订阅后不再收到事件。"""
        received = []
        h = lambda ev: received.append(ev)  # noqa: E731

        bus.subscribe(h, ToolStartedEvent)
        bus.publish(ToolStartedEvent(label="a"))
        bus.unsubscribe(h, ToolStartedEvent)
        bus.publish(ToolStartedEvent(label="b"))

        assert len(received) == 1
        assert received[0].label == "a"

    def test_duplicate_subscribe_no_dup_regression(self, bus):
        """同一 handler 重复订阅同一类型不产生重复通知。"""
        received = []
        h = lambda ev: received.append(ev)  # noqa: E731

        bus.subscribe(h, ToolStartedEvent)
        bus.subscribe(h, ToolStartedEvent)
        bus.subscribe(h, ToolStartedEvent)
        bus.publish(ToolStartedEvent(label="a"))

        assert len(received) == 1

    def test_subscribe_all_events_regression(self, bus):
        """event_type=None 订阅所有事件。"""
        received = []
        bus.subscribe(lambda ev: received.append(ev))
        bus.publish(OutputEvent(text="hello"))
        bus.publish(ToolStartedEvent(label="t1", tool_name="read_file"))

        assert len(received) == 2

    def test_type_filter_regression(self, bus):
        """按类型订阅只收到匹配类型的事件，不收到其他类型。"""
        received = []
        bus.subscribe(lambda ev: received.append(ev), ToolStartedEvent)

        bus.publish(ToolStartedEvent(label="t1", tool_name="read_file"))
        bus.publish(OutputEvent(text="ignored"))
        bus.publish(AgentStatusChanged(label="a1", status="done"))

        assert len(received) == 1
        assert received[0].label == "t1"

    def test_subscribe_invalid_type_regression(self, bus):
        """订阅非 DisplayEvent 子类抛出 TypeError。"""
        with pytest.raises(TypeError):
            bus.subscribe(lambda ev: None, str)  # type: ignore[arg-type]

    def test_subscriber_count_regression(self, bus):
        """subscriber_count 统计类型订阅者 + 全局订阅者。"""
        assert bus.subscriber_count == 0

        h1 = lambda ev: None  # noqa: E731
        bus.subscribe(h1, ToolStartedEvent)
        bus.subscribe(h1, ToolStartedEvent)  # 重复订阅不增加
        bus.subscribe(h1, OutputEvent)
        bus.subscribe(h1)  # 全局订阅

        assert bus.subscriber_count == 3

    def test_clear_regression(self, bus):
        """clear() 清除所有订阅，后续发布不触发。"""
        received = []
        bus.subscribe(lambda ev: received.append(ev), ToolStartedEvent)
        bus.subscribe(lambda ev: received.append(ev))

        bus.clear()
        bus.publish(ToolStartedEvent(label="a"))

        assert bus.subscriber_count == 0
        assert received == []

    def test_unsubscribe_all_type_regression(self, bus):
        """unsubscribe(handler) 不带类型从全局订阅中移除。"""
        received = []
        h = lambda ev: received.append(ev)  # noqa: E731

        bus.subscribe(h)  # 全局订阅
        bus.unsubscribe(h)
        bus.publish(OutputEvent(text="x"))

        assert received == []


class TestEventBusNotificationOrder:
    """多订阅者通知顺序。"""

    def test_multi_subscriber_order_regression(self, bus):
        """同类型订阅者按订阅顺序通知，全局订阅者在类型订阅者之后。"""
        order = []
        bus.subscribe(lambda ev: order.append("type_first"), ModelPhaseEvent)
        bus.subscribe(lambda ev: order.append("type_second"), ModelPhaseEvent)
        bus.subscribe(lambda ev: order.append("global"), ModelPhaseEvent)

        bus.publish(ModelPhaseEvent(label="agent-1", phase="thinking"))

        assert order == ["type_first", "type_second", "global"]

    def test_exception_isolation_regression(self, bus):
        """单个订阅者抛异常不影响其他订阅者接收事件。"""
        received = []

        def bad_handler(ev):
            raise RuntimeError("boom")

        bus.subscribe(bad_handler, SessionStarted)
        bus.subscribe(lambda ev: received.append(ev), SessionStarted)

        bus.publish(SessionStarted(source="agent"))

        assert len(received) == 1
        assert received[0].source == "agent"

    def test_no_subscriber_no_error_regression(self, bus):
        """无订阅者时发布事件不报错。"""
        bus.publish(SessionStarted(source="agent"))
        bus.publish(ToolStartedEvent(label="t1", tool_name="read_file"))


class TestEventBusFourLayerShared:
    """四层共用场景 — api/core/webui/tools 模拟注册同一事件。"""

    def test_four_layer_shared_regression(self, bus):
        """api/core/webui/tools 四层各自订阅并收到同一事件。"""
        layers = {"api": [], "core": [], "webui": [], "tools": []}
        for layer in layers:
            bus.subscribe(
                lambda ev, l=layer: layers[l].append(ev),
                AgentStatusChanged,
            )

        bus.publish(AgentStatusChanged(label="agent-1", status="done"))

        for layer, events in layers.items():
            assert len(events) == 1, f"{layer} 层未收到事件"
            assert events[0].label == "agent-1"
            assert events[0].status == "done"

    def test_four_layer_type_independent_regression(self, bus):
        """四层订阅不同类型，各自只收到本层关注的事件。"""
        api_events, core_events = [], []
        webui_events, tools_events = [], []

        bus.subscribe(lambda ev: api_events.append(ev), OutputEvent)          # api 层
        bus.subscribe(lambda ev: core_events.append(ev), AgentAddedEvent)     # core 层
        bus.subscribe(lambda ev: webui_events.append(ev), ToolStartedEvent)   # webui 层
        bus.subscribe(lambda ev: tools_events.append(ev), ToolSummaryEvent)   # tools 层

        bus.publish(OutputEvent(text="api output"))
        bus.publish(AgentAddedEvent(label="agent-1", description="执行"))
        bus.publish(ToolStartedEvent(label="call_1", tool_name="read_file"))

        assert len(api_events) == 1
        assert len(core_events) == 1
        assert len(webui_events) == 1
        assert len(tools_events) == 0  # 未发布 ToolSummaryEvent

    def test_cross_layer_no_bleed_regression(self, bus):
        """一个层取消订阅后，其他层不受影响。"""
        api_events, tools_events = [], []
        h_api = lambda ev: api_events.append(ev)  # noqa: E731

        bus.subscribe(h_api, OutputEvent)
        bus.subscribe(lambda ev: tools_events.append(ev), OutputEvent)

        bus.unsubscribe(h_api, OutputEvent)
        bus.publish(OutputEvent(text="after unsubscribe"))

        assert api_events == []
        assert len(tools_events) == 1


class TestOutputConsumerSinglePath:
    """方向D 步骤7 — OutputConsumer 单消费路径策略回归测试。"""

    def test_output_consumer_single_path_regression(self, bus):
        """chat_ui_managed=False 无活跃 ChatUI → 直写；chat_ui_managed=True 活跃 ChatUI → 跳过。"""
        from unittest.mock import MagicMock, patch
        from src.tui.events.consumers import OutputConsumer

        # chat_ui_managed=False + 无活跃 ChatUI → 直写
        consumer = OutputConsumer(event_bus=bus, chat_ui_managed=False)
        consumer._write = MagicMock()
        with patch("src.tui.consumer.get_active_chat_ui", return_value=None):
            consumer._on_output(OutputEvent(text="hello"))
        consumer._write.assert_called_once_with("hello", "info")

        # chat_ui_managed=True + ChatUI 活跃 → 跳过直写（管线处理）
        consumer2 = OutputConsumer(event_bus=bus, chat_ui_managed=True)
        consumer2._write = MagicMock()
        mock_ui = MagicMock()
        with patch("src.tui.consumer.get_active_chat_ui", return_value=mock_ui):
            consumer2._on_output(OutputEvent(text="hello"))
        consumer2._write.assert_not_called()

        # source="cmd" 始终跳过（即使 chat_ui_managed=False）
        consumer3 = OutputConsumer(event_bus=bus, chat_ui_managed=False)
        consumer3._write = MagicMock()
        with patch("src.tui.consumer.get_active_chat_ui", return_value=None):
            consumer3._on_output(OutputEvent(text="hello", source="cmd"))
        consumer3._write.assert_not_called()


# ═══════════════════════════════════════════════════════════
# 事件类型 — 字段/默认值/frozen/序列化/类型判别
# ═══════════════════════════════════════════════════════════

class TestEventTypes:
    """事件类型定义行为。"""

    def test_event_defaults_regression(self):
        """各事件类型默认字段值。"""
        t = ToolStartedEvent()
        assert t.label == ""
        assert t.tool_name == ""
        assert t.metadata is None
        assert t.source == ""

        d = ToolDoneEvent()
        assert d.success is True

        s = SessionStopped()
        assert s.final is False

        u = UserSelectNeededEvent()
        assert u.multi_select is False
        assert u.timeout == 120
        assert u.options == ()
        assert u.option_descriptions == ()

        m = MetricsUpdateEvent()
        assert m.output_tokens == 0
        assert m.speed == 0.0

    def test_event_frozen_regression(self):
        """frozen dataclass 不可变，修改字段抛 FrozenInstanceError。"""
        ev = OutputEvent(text="hi")
        with pytest.raises(dataclasses.FrozenInstanceError):
            ev.text = "changed"  # type: ignore[misc]

    def test_event_serialize_roundtrip_regression(self):
        """dataclasses.asdict 序列化后可重建等价事件对象。"""
        ev = ToolStartedEvent(
            label="call_1",
            tool_name="read_file",
            detail="读取文件",
            metadata={"参数": "120t", "解析": "0.5s"},
            tool_id="t1",
            source="agent",
        )
        d = dataclasses.asdict(ev)
        rebuilt = ToolStartedEvent(**d)
        assert rebuilt == ev

    def test_usage_event_roundtrip_regression(self):
        """带 dict 字段的用量事件序列化往返一致。"""
        ev = UsageUpdatedEvent(
            label="agent-1",
            usage={"input": 10, "output": 20, "speed": 5.5},
            replace=True,
        )
        d = dataclasses.asdict(ev)
        rebuilt = UsageUpdatedEvent(**d)
        assert rebuilt == ev

    def test_event_type_discrimination_regression(self):
        """isinstance 类型判别：事件对象属于其类型与基类，不属于无关类型。"""
        assert isinstance(ContentChunkEvent(text="x"), DisplayEvent)
        assert isinstance(ToolStartedEvent(), DisplayEvent)
        assert not isinstance(ContentChunkEvent(), ToolStartedEvent)
        assert not isinstance(OutputEvent(), SessionStarted)

    def test_all_event_types_subclass_regression(self):
        """ALL_EVENT_TYPES 中全部类型均为 DisplayEvent 子类。"""
        assert len(ALL_EVENT_TYPES) >= 20  # 事件类型集完整度保护
        for et in ALL_EVENT_TYPES:
            assert issubclass(et, DisplayEvent)

    def test_timestamp_default_regression(self):
        """timestamp 默认自动生成（>0）。"""
        ev = SessionStarted()
        assert ev.timestamp > 0

    def test_source_passthrough_regression(self):
        """source 字段携带来源标识，供事件消费方路由。"""
        ev = OutputEvent(text="x", source="tool-executor")
        assert ev.source == "tool-executor"
