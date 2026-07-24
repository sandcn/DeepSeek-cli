"""测试 DisplayEventBus 事件发布-订阅完整链路。

覆盖场景：
  - 各种事件类型通过 DisplayEventBus 发布和订阅
  - 批量发布（register_batched_event）
  - 事件 source 字段传递
  - 异常隔离（单个 handler 抛异常不影响其他）
  - 同一 handler 多事件类型注册
  - subscriber_count 准确计数
  - clear 清空所有状态
  - reset_default 后独立隔离
"""

from __future__ import annotations

import time
from typing import Any, List

import pytest

from src.tui.events.event_bus import DisplayEventBus
from src.tui.events.event_types import (
    DisplayEvent,
    SessionStarted, SessionStopped,
    ToolParsingEvent, ToolStartedEvent, ToolDoneEvent,
    ToolOutputChunkEvent, ToolBatchStartedEvent,
    AgentAddedEvent, AgentStatusChanged,
    ModelPhaseEvent, PhaseDoneEvent, UsageUpdatedEvent,
    ContentChunkEvent, ReasoningChunkEvent,
    ParseInfoEvent, ParseInfoDoneEvent, MetricsUpdateEvent,
    OutputEvent, ToolSummaryEvent,
    UserSelectNeededEvent, AgentResultEvent,
)


# ═══════════════════════════════════════════════════════════
# 辅助
# ═══════════════════════════════════════════════════════════

class EventCollector:
    """收集发布的事件用于断言。"""

    def __init__(self):
        self.events: List[DisplayEvent] = []

    def handler(self, event: DisplayEvent) -> None:
        self.events.append(event)

    @property
    def count(self) -> int:
        return len(self.events)


class TestDisplayEventBusBasic:
    """DisplayEventBus 基本发布-订阅测试。"""

    def setup_method(self):
        self.bus = DisplayEventBus()

    def test_publish_and_subscribe_type_specific(self):
        """按类型订阅并接收对应事件。"""
        collector = EventCollector()
        self.bus.subscribe(collector.handler, event_type=SessionStarted)

        self.bus.publish(SessionStarted(source="test"))
        assert collector.count == 1

    def test_publish_and_subscribe_all(self):
        """订阅所有事件类型。"""
        collector = EventCollector()
        self.bus.subscribe(collector.handler)  # event_type=None → 所有事件

        self.bus.publish(SessionStarted(source="test"))
        self.bus.publish(ToolParsingEvent(label="x"))
        assert collector.count == 2

    def test_type_filtering(self):
        """仅接收订阅类型的事件，不接收其他类型。"""
        collector = EventCollector()
        self.bus.subscribe(collector.handler, event_type=SessionStarted)

        self.bus.publish(SessionStarted(source="a"))
        self.bus.publish(ToolParsingEvent(label="x"))  # 不应被接收
        assert collector.count == 1

    def test_no_handler_no_error(self):
        """无订阅者时发布事件不抛异常。"""
        self.bus.publish(SessionStarted(source="test"))

    def test_source_field_preserved(self):
        """事件的 source 字段在发布后保持。"""
        collector = EventCollector()
        self.bus.subscribe(collector.handler, event_type=SessionStarted)

        self.bus.publish(SessionStarted(source="parallel-display"))
        assert collector.events[0].source == "parallel-display"


class TestDisplayEventBusMultipleTypes:
    """多种事件类型发布测试。"""

    def setup_method(self):
        self.bus = DisplayEventBus()

    def test_publish_lifecycle_events(self):
        """发布生命周期事件。"""
        events: List[DisplayEvent] = []
        self.bus.subscribe(lambda e: events.append(e))

        self.bus.publish(SessionStarted(source="test"))
        self.bus.publish(SessionStopped(final=True, source="test"))

        assert len(events) == 2
        assert isinstance(events[0], SessionStarted)
        assert isinstance(events[1], SessionStopped)
        assert events[1].final is True

    def test_publish_tool_events(self):
        """发布工具调用事件。"""
        events: List[DisplayEvent] = []
        self.bus.subscribe(lambda e: events.append(e))

        self.bus.publish(ToolParsingEvent(label="call_1", tool_name="read_file"))
        self.bus.publish(ToolStartedEvent(label="call_1", tool_name="read_file"))
        self.bus.publish(ToolDoneEvent(label="call_1", tool_name="read_file", success=True))

        assert len(events) == 3
        assert isinstance(events[0], ToolParsingEvent)
        assert isinstance(events[1], ToolStartedEvent)
        assert isinstance(events[2], ToolDoneEvent)

    def test_publish_agent_events(self):
        """发布 Agent 状态事件。"""
        events: List[DisplayEvent] = []
        self.bus.subscribe(lambda e: events.append(e))

        self.bus.publish(AgentAddedEvent(label="agent-1", description="Worker"))
        self.bus.publish(AgentStatusChanged(label="agent-1", status="done"))

        assert len(events) == 2
        assert isinstance(events[0], AgentAddedEvent)
        assert events[0].description == "Worker"
        assert isinstance(events[1], AgentStatusChanged)

    def test_publish_model_events(self):
        """发布模型阶段事件。"""
        events: List[DisplayEvent] = []
        self.bus.subscribe(lambda e: events.append(e))

        self.bus.publish(ModelPhaseEvent(label="agent-1", phase="thinking"))
        self.bus.publish(PhaseDoneEvent(label="agent-1", phase="reasoning"))
        self.bus.publish(UsageUpdatedEvent(label="agent-1", usage={"output": 100}))

        assert len(events) == 3

    def test_publish_content_events(self):
        """发布流式内容事件。"""
        events: List[DisplayEvent] = []
        self.bus.subscribe(lambda e: events.append(e))

        self.bus.publish(ContentChunkEvent(text="Hello", label="agent-1"))
        self.bus.publish(ReasoningChunkEvent(text="thinking...", label="agent-1"))

        assert len(events) == 2

    def test_publish_output_events(self):
        """发布通用输出事件。"""
        events: List[DisplayEvent] = []
        self.bus.subscribe(lambda e: events.append(e))

        self.bus.publish(OutputEvent(text="Hello", level="info"))
        self.bus.publish(ToolSummaryEvent(
            successful_tools=("read_file",),
            failed_tools=(("search", "Not found"),),
        ))
        self.bus.publish(AgentResultEvent(label="agent-1", result="Done"))

        assert len(events) == 3
        assert isinstance(events[1], ToolSummaryEvent)

    def test_publish_all_21_event_types(self):
        """21 种事件类型均能正常发布。"""
        events: List[DisplayEvent] = []
        self.bus.subscribe(lambda e: events.append(e))

        # 构造所有 21 种事件并发布
        all_events = [
            SessionStarted(source="test"),
            SessionStopped(final=True),
            ToolParsingEvent(label="x", tool_name="read"),
            ToolStartedEvent(label="x", tool_name="read"),
            ToolDoneEvent(label="x", tool_name="read", success=True),
            ToolOutputChunkEvent(label="x", text="data"),
            ToolBatchStartedEvent(label="x", tool_names=("a", "b")),
            AgentAddedEvent(label="x", description="test"),
            AgentStatusChanged(label="x", status="done"),
            ModelPhaseEvent(label="x", phase="thinking"),
            PhaseDoneEvent(label="x", phase="reasoning"),
            UsageUpdatedEvent(label="x", usage={"o": 1}),
            ContentChunkEvent(text="hello"),
            ReasoningChunkEvent(text="thinking"),
            ParseInfoEvent(label="x", tool_names="read"),
            ParseInfoDoneEvent(label="x"),
            MetricsUpdateEvent(label="x", output_tokens=100),
            OutputEvent(text="info", level="info"),
            ToolSummaryEvent(successful_tools=("a",)),
            UserSelectNeededEvent(select_id="s1"),
            AgentResultEvent(label="x", result="done"),
        ]

        for e in all_events:
            self.bus.publish(e)

        assert len(events) == 21, (
            f"应收到 21 个事件，实际 {len(events)}"
        )


class TestDisplayEventBusSubscribeUnsubscribe:
    """订阅与取消订阅测试。"""

    def setup_method(self):
        self.bus = DisplayEventBus()

    def test_subscribe_and_unsubscribe_type(self):
        """按类型取消订阅后不再接收。"""
        collector = EventCollector()
        self.bus.subscribe(collector.handler, event_type=SessionStarted)
        self.bus.unsubscribe(collector.handler, event_type=SessionStarted)

        self.bus.publish(SessionStarted(source="test"))
        assert collector.count == 0

    def test_subscribe_and_unsubscribe_all(self):
        """取消所有订阅后不再接收。"""
        collector = EventCollector()
        self.bus.subscribe(collector.handler)  # 订阅所有
        self.bus.unsubscribe(collector.handler)  # 取消所有

        self.bus.publish(SessionStarted(source="test"))
        assert collector.count == 0

    def test_unsubscribe_nonexistent_no_error(self):
        """取消不存在的订阅不抛异常。"""
        collector = EventCollector()
        self.bus.unsubscribe(collector.handler)  # 不应抛异常
        self.bus.unsubscribe(collector.handler, event_type=SessionStarted)  # 不应抛异常

    def test_same_handler_multiple_types(self):
        """同一 handler 注册多个事件类型。"""
        collector = EventCollector()
        self.bus.subscribe(collector.handler, event_type=SessionStarted)
        self.bus.subscribe(collector.handler, event_type=SessionStopped)

        self.bus.publish(SessionStarted(source="a"))
        self.bus.publish(SessionStopped(final=True))
        assert collector.count == 2

    def test_unsubscribe_one_type_keeps_other(self):
        """取消一个类型后，另一类型的订阅仍有效。"""
        collector = EventCollector()
        self.bus.subscribe(collector.handler, event_type=SessionStarted)
        self.bus.subscribe(collector.handler, event_type=SessionStopped)

        self.bus.unsubscribe(collector.handler, event_type=SessionStarted)

        self.bus.publish(SessionStarted(source="a"))  # 不应接收
        self.bus.publish(SessionStopped(final=True))  # 仍应接收
        assert collector.count == 1
        assert isinstance(collector.events[0], SessionStopped)


class TestDisplayEventBusExceptionIsolation:
    """异常隔离测试。"""

    def setup_method(self):
        self.bus = DisplayEventBus()

    def test_exception_in_one_handler_does_not_affect_others(self):
        """单个 handler 抛异常不影响其他 handler。"""
        received: List[DisplayEvent] = []

        def handler_ok(event: DisplayEvent) -> None:
            received.append(event)

        def handler_broken(event: DisplayEvent) -> None:
            raise ValueError("broken!")

        self.bus.subscribe(handler_ok, event_type=SessionStarted)
        self.bus.subscribe(handler_broken, event_type=SessionStarted)

        # 不应抛异常
        self.bus.publish(SessionStarted(source="test"))
        # handler_ok 仍应收到事件
        assert len(received) == 1


class TestDisplayEventBusSubscriberCount:
    """subscriber_count 准确性测试。"""

    def setup_method(self):
        self.bus = DisplayEventBus()

    def test_zero_subscribers(self):
        assert self.bus.subscriber_count == 0

    def test_one_subscriber(self):
        self.bus.subscribe(lambda e: None, event_type=SessionStarted)
        assert self.bus.subscriber_count == 1

    def test_two_subscribers_same_type(self):
        self.bus.subscribe(lambda e: None, event_type=SessionStarted)
        self.bus.subscribe(lambda e: None, event_type=SessionStarted)
        assert self.bus.subscriber_count == 2

    def test_subscriber_count_after_unsubscribe(self):
        h = lambda e: None
        self.bus.subscribe(h, event_type=SessionStarted)
        self.bus.unsubscribe(h, event_type=SessionStarted)
        assert self.bus.subscriber_count == 0

    def test_subscriber_count_multiple_types(self):
        self.bus.subscribe(lambda e: None, event_type=SessionStarted)
        self.bus.subscribe(lambda e: None, event_type=SessionStopped)
        assert self.bus.subscriber_count == 2


class TestDisplayEventBusClear:
    """clear() 清空所有状态测试。"""

    def setup_method(self):
        self.bus = DisplayEventBus()

    def test_clear_removes_all_subscribers(self):
        collector = EventCollector()
        self.bus.subscribe(collector.handler, event_type=SessionStarted)
        self.bus.subscribe(collector.handler, event_type=SessionStopped)

        self.bus.clear()

        self.bus.publish(SessionStarted(source="a"))
        self.bus.publish(SessionStopped(final=True))
        assert collector.count == 0
        assert self.bus.subscriber_count == 0

    def test_clear_empty_bus(self):
        """清空空 bus 不抛异常。"""
        self.bus.clear()  # 不应抛异常

    def test_re_subscribe_after_clear(self):
        """clear 后可重新订阅。"""
        self.bus.clear()

        collector = EventCollector()
        self.bus.subscribe(collector.handler, event_type=SessionStarted)
        self.bus.publish(SessionStarted(source="test"))
        assert collector.count == 1


class TestDisplayEventBusDefaultInstance:
    """get_default() / reset_default() 单例管理测试。"""

    def setup_method(self):
        # 每次测试前重置默认实例
        DisplayEventBus.reset_default()

    def test_get_default_singleton(self):
        """get_default() 返回同一实例。"""
        bus1 = DisplayEventBus.get_default()
        bus2 = DisplayEventBus.get_default()
        assert bus1 is bus2

    def test_reset_default_creates_new_instance(self):
        """reset_default 后 get_default 返回新实例。"""
        bus_before = DisplayEventBus.get_default()
        DisplayEventBus.reset_default()
        bus_after = DisplayEventBus.get_default()
        assert bus_before is not bus_after

    def test_reset_default_clears_subscribers(self):
        """reset_default 后旧订阅被清除。"""
        bus = DisplayEventBus.get_default()
        collector = EventCollector()
        bus.subscribe(collector.handler, event_type=SessionStarted)

        DisplayEventBus.reset_default()
        new_bus = DisplayEventBus.get_default()
        new_bus.publish(SessionStarted(source="test"))
        # 新的实例没有订阅者
        assert new_bus.subscriber_count == 0


class TestDisplayEventBusBatch:
    """批量事件发布测试（register_batched_event）。"""

    def setup_method(self):
        self.bus = DisplayEventBus()

    def test_register_batched_event(self):
        """注册批处理事件不抛异常。"""
        self.bus.register_batched_event(ContentChunkEvent)

    def test_unregister_batched_event(self):
        """取消注册批处理事件不抛异常。"""
        self.bus.register_batched_event(ContentChunkEvent)
        self.bus.unregister_batched_event(ContentChunkEvent)

    def test_batched_event_type_check(self):
        """非 DisplayEvent 子类注册时抛 TypeError。"""
        with pytest.raises(TypeError):
            self.bus.register_batched_event(str)  # type: ignore[arg-type]

        with pytest.raises(TypeError):
            self.bus.unregister_batched_event(int)  # type: ignore[arg-type]

    def test_batched_content_chunk_publish(self):
        """注册批处理的 ContentChunkEvent 可正常发布。"""
        self.bus.register_batched_event(ContentChunkEvent)
        collector = EventCollector()
        self.bus.subscribe(collector.handler, event_type=ContentChunkEvent)

        self.bus.publish(ContentChunkEvent(text="Hello"))
        # 等待批处理窗口（~33ms）后再验证事件到达
        time.sleep(0.05)
        assert collector.count >= 1, \
            f"批处理后应收到 ContentChunkEvent，实际收到 {collector.count} 个事件"


class TestDisplayEventBusEdgeCases:
    """边缘场景测试。"""

    def setup_method(self):
        self.bus = DisplayEventBus()

    def test_subscribe_invalid_event_type(self):
        """非 DisplayEvent 子类订阅抛 TypeError。"""
        with pytest.raises(TypeError):
            self.bus.subscribe(lambda e: None, event_type=str)  # type: ignore[arg-type]

    def test_publish_no_source(self):
        """无 source 的事件发布不抛异常。"""
        collector = EventCollector()
        self.bus.subscribe(collector.handler)
        self.bus.publish(SessionStarted())
        assert collector.count == 1

    def test_multiple_handlers_same_event(self):
        """多个 handler 订阅同一事件类型。"""
        events1: List[DisplayEvent] = []
        events2: List[DisplayEvent] = []

        def h1(e: DisplayEvent) -> None:
            events1.append(e)

        def h2(e: DisplayEvent) -> None:
            events2.append(e)

        self.bus.subscribe(h1, event_type=SessionStarted)
        self.bus.subscribe(h2, event_type=SessionStarted)

        self.bus.publish(SessionStarted(source="test"))

        assert len(events1) == 1
        assert len(events2) == 1
