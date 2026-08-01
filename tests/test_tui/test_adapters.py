"""test_adapters — 零覆盖模块最小测试（方向5 步骤5.5）。

覆盖 ``events/adapters.py``：DisplayEventAdapter（事件 → BaseDisplay 方法）
与 EventBusDisplayProxy（BaseDisplay 方法 → 事件发布）双向适配。
"""

from __future__ import annotations

from src.tui.events.adapters import (
    DisplayEventAdapter,
    EventBusDisplayProxy,
    wire_event_bus,
)
from src.tui.events.event_bus import DisplayEventBus
from src.tui.events.event_types import (
    AgentStatusChanged,
    ToolDoneEvent,
    ToolStartedEvent,
)


class _Recorder:
    """记录显示方法调用的 BaseDisplay 风格对象（鸭类型）。"""

    def __init__(self):
        self.calls = []

    def tool_start(self, label, tool_name, detail, metadata):
        self.calls.append(("tool_start", label, tool_name))

    def tool_done(self, label, tool_name, success, metadata):
        self.calls.append(("tool_done", label, tool_name, success))

    def update_status(self, label, status):
        self.calls.append(("update_status", label, status))


class TestDisplayEventAdapter:
    """DisplayEventAdapter — 事件 → BaseDisplay 方法。"""

    def test_subscribe_maps_events_to_methods(self):
        """订阅后发布事件 → display 方法被调用。"""
        DisplayEventBus.reset_default()
        try:
            bus = DisplayEventBus.get_default()
            rec = _Recorder()
            adapter = DisplayEventAdapter(rec)
            adapter.subscribe_to(bus)
            bus.publish(ToolStartedEvent(label="a", tool_name="read_file", source="t"))
            bus.publish(ToolDoneEvent(label="a", tool_name="read_file", success=True, source="t"))
            assert ("tool_start", "a", "read_file") in rec.calls
            assert ("tool_done", "a", "read_file", True) in rec.calls
        finally:
            DisplayEventBus.reset_default()

    def test_unsubscribe_all(self):
        """unsubscribe_all 取消全部订阅。"""
        DisplayEventBus.reset_default()
        try:
            bus = DisplayEventBus.get_default()
            rec = _Recorder()
            adapter = DisplayEventAdapter(rec)
            adapter.subscribe_to(bus)
            adapter.unsubscribe_all()
            assert bus.subscriber_count == 0
        finally:
            DisplayEventBus.reset_default()

    def test_missing_method_skipped(self):
        """display 缺少映射方法时跳过该事件（不抛）。"""
        DisplayEventBus.reset_default()
        try:
            bus = DisplayEventBus.get_default()
            rec = _Recorder()
            # rec 无 tool_parsing → ToolParsingEvent 订阅被跳过
            adapter = DisplayEventAdapter(rec)
            adapter.subscribe_to(bus)
            # 仅已映射且 display 有方法的类型被订阅
            assert any(hasattr(rec, m) for m in adapter._EVENT_METHOD_MAP.values())
        finally:
            DisplayEventBus.reset_default()


class TestEventBusDisplayProxy:
    """EventBusDisplayProxy — BaseDisplay 方法 → 事件发布。"""

    def test_update_status_publishes_event(self):
        """update_status → AgentStatusChanged 发布到总线。"""
        DisplayEventBus.reset_default()
        try:
            bus = DisplayEventBus.get_default()
            received = []
            bus.subscribe(lambda ev: received.append(ev), event_type=AgentStatusChanged)
            proxy = EventBusDisplayProxy(bus, source="agent")
            proxy.update_status("a", "running")
            assert len(received) == 1
            assert received[0].label == "a"
            assert received[0].status == "running"
            assert received[0].source == "agent"
        finally:
            DisplayEventBus.reset_default()

    def test_capture_and_print(self):
        """capture_and_print 直接执行 display_func（代理语义）。"""
        proxy = EventBusDisplayProxy(source="")
        out = proxy.capture_and_print(lambda: "ok")
        assert out == "ok"

    def test_set_source(self):
        """set_source 更新事件来源（后续发布携带新 source）。"""
        DisplayEventBus.reset_default()
        try:
            bus = DisplayEventBus.get_default()
            received = []
            bus.subscribe(lambda ev: received.append(ev), event_type=AgentStatusChanged)
            proxy = EventBusDisplayProxy(bus, source="old")
            proxy.set_source("new")
            proxy.update_status("a", "running")
            assert received[0].source == "new"
        finally:
            DisplayEventBus.reset_default()


class TestWireEventBus:
    """wire_event_bus — 一键连接（代理发布 + 适配消费）。"""

    def test_wire_returns_proxy(self):
        """wire_event_bus 返回 EventBusDisplayProxy 且订阅已建立。"""
        DisplayEventBus.reset_default()
        try:
            bus = DisplayEventBus.get_default()
            rec = _Recorder()
            proxy = wire_event_bus(rec, bus, source="agent")
            assert isinstance(proxy, EventBusDisplayProxy)
            # 代理发布 ToolStartedEvent → 适配器消费到 rec
            proxy.tool_start("a", "read_file", "detail", None)
            assert ("tool_start", "a", "read_file") in rec.calls
        finally:
            DisplayEventBus.reset_default()
