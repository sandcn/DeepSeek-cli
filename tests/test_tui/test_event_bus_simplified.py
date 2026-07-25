"""测试 DisplayEventBus 简化后的订阅/发布行为。

覆盖场景：
- 按类型订阅和取消订阅
- 全量（所有事件）订阅和取消订阅
- 同一 handler 多事件类型注册
- 发布事件正确分发
- clear 清空所有状态
- subscriber_count 准确计数
"""

from __future__ import annotations

from src.tui.events.event_bus import DisplayEventBus
from src.tui.events.event_types import (
    DisplayEvent,
    SessionStarted,
    ToolStartedEvent,
    ToolDoneEvent,
    OutputEvent,
    ContentChunkEvent,
)


class TestDisplayEventBusSimplified:
    """测试简化后 DisplayEventBus 的订阅/发布核心行为。"""

    def setup_method(self):
        """每个测试前创建新实例。"""
        self.bus = DisplayEventBus()

    # ── 按类型订阅 ──────────────────────────────────────

    def test_subscribe_by_type_receives_event(self):
        """按类型订阅后，发布该类型事件时 handler 被调用。"""
        received = []

        def handler(event):
            received.append(event)

        self.bus.subscribe(handler, event_type=ToolStartedEvent)
        event = ToolStartedEvent(label="test", tool_name="search")
        self.bus.publish(event)

        assert len(received) == 1
        assert received[0] is event

    def test_subscribe_by_type_ignores_other_types(self):
        """按类型订阅后，其他类型事件不会触发 handler。"""
        received = []

        def handler(event):
            received.append(event)

        self.bus.subscribe(handler, event_type=ToolStartedEvent)
        self.bus.publish(ToolDoneEvent(label="other"))

        assert len(received) == 0

    # ── 全量订阅 ────────────────────────────────────────

    def test_subscribe_all_receives_all_events(self):
        """订阅所有事件后，任何类型事件都能收到。"""
        received = []

        def handler(event):
            received.append(event)

        self.bus.subscribe(handler)  # event_type=None → 全量订阅
        self.bus.publish(ToolStartedEvent(label="a"))
        self.bus.publish(ToolDoneEvent(label="b"))

        assert len(received) == 2

    def test_subscribe_all_respects_event_sources(self):
        """全量订阅收到所有来源的事件。"""
        received = []

        def handler(event):
            received.append(event)

        self.bus.subscribe(handler)
        self.bus.publish(ToolStartedEvent(label="a", source="agent1"))
        self.bus.publish(OutputEvent(text="hello", source="agent2"))

        assert len(received) == 2

    # ── 取消订阅 ────────────────────────────────────────

    def test_unsubscribe_by_type(self):
        """按类型取消订阅后，handler 不再被调用。"""
        received = []

        def handler(event):
            received.append(event)

        self.bus.subscribe(handler, event_type=ToolStartedEvent)
        self.bus.unsubscribe(handler, event_type=ToolStartedEvent)
        self.bus.publish(ToolStartedEvent(label="a"))

        assert len(received) == 0

    def test_unsubscribe_all(self):
        """取消全量订阅后，handler 不再被调用。"""
        received = []

        def handler(event):
            received.append(event)

        self.bus.subscribe(handler)  # 全量订阅
        self.bus.unsubscribe(handler)  # 取消全量
        self.bus.publish(ToolStartedEvent(label="a"))

        assert len(received) == 0

    def test_unsubscribe_nonexistent_handler(self):
        """取消未注册的 handler 不报错。"""

        def handler(event):
            pass

        self.bus.unsubscribe(handler, event_type=ToolStartedEvent)
        self.bus.unsubscribe(handler)  # 全量取消也不报错

    def test_unsubscribe_partial_keeps_other_subscriptions(self):
        """取消按类型订阅后，同一 handler 的全量订阅仍在。"""
        type_received = []
        all_received = []

        def handler(event):
            type_received.append(event)
            all_received.append(event)

        # 先注册类型订阅
        self.bus.subscribe(handler, event_type=ToolStartedEvent)
        # 再注册全量订阅
        self.bus.subscribe(handler)  # event_type=None

        # 取消类型订阅
        self.bus.unsubscribe(handler, event_type=ToolStartedEvent)

        # 发布事件 — handler 通过全量订阅仍应收到
        self.bus.publish(ToolStartedEvent(label="a"))

        assert len(all_received) == 1
        # type_received 不会再增加

    # ── 同一 handler 多事件类型 ─────────────────────────

    def test_same_handler_multiple_types(self):
        """同一 handler 可在多个事件类型上注册。"""
        received = []

        def handler(event):
            received.append(event)

        self.bus.subscribe(handler, event_type=ToolStartedEvent)
        self.bus.subscribe(handler, event_type=ToolDoneEvent)

        self.bus.publish(ToolStartedEvent(label="a"))
        self.bus.publish(ToolDoneEvent(label="b"))

        # handler 被执行两次（每个事件类型各一次）
        assert len(received) == 2

    def test_same_handler_type_and_all(self):
        """同一 handler 同时注册按类型和全量订阅，两个路径各执行一次。"""
        received = []

        def handler(event):
            received.append(event)

        # 先注册按类型，再注册全量
        self.bus.subscribe(handler, event_type=ToolStartedEvent)
        self.bus.subscribe(handler)  # 全量

        # 发布 ToolStartedEvent — handler 通过两个独立 wrapper 被调用
        # 每个 subscription 创建独立的 wrapper，CoreEventBus 视为不同 handler
        self.bus.publish(ToolStartedEvent(label="a"))

        assert len(received) == 2

    # ── clear ───────────────────────────────────────────

    def test_clear_removes_all_subscriptions(self):
        """clear() 清除所有订阅。"""
        received = []

        def handler(event):
            received.append(event)

        self.bus.subscribe(handler, event_type=ToolStartedEvent)
        self.bus.subscribe(handler)  # 全量

        self.bus.clear()

        self.bus.publish(ToolStartedEvent(label="a"))

        assert len(received) == 0

    def test_clear_resets_subscriber_count(self):
        """clear() 后 subscriber_count 为 0。"""
        def handler(event):
            pass

        self.bus.subscribe(handler, event_type=ToolStartedEvent)
        assert self.bus.subscriber_count > 0

        self.bus.clear()
        assert self.bus.subscriber_count == 0

    # ── subscriber_count ────────────────────────────────

    def test_subscriber_count_type(self):
        """按类型订阅后 subscriber_count 增加。"""
        def h1(event):
            pass

        assert self.bus.subscriber_count == 0
        self.bus.subscribe(h1, event_type=ToolStartedEvent)
        assert self.bus.subscriber_count == 1

    def test_subscriber_count_multiple_handlers(self):
        """多个 handler 注册后 subscriber_count 累加。"""
        def h1(event):
            pass

        def h2(event):
            pass

        self.bus.subscribe(h1, event_type=ToolStartedEvent)
        self.bus.subscribe(h2, event_type=ToolDoneEvent)
        assert self.bus.subscriber_count == 2

    def test_subscriber_count_all_subscription(self):
        """全量订阅计入 subscriber_count。"""
        def handler(event):
            pass

        self.bus.subscribe(handler)  # 全量
        assert self.bus.subscriber_count == 1

    def test_subscriber_count_after_unsubscribe(self):
        """取消订阅后 subscriber_count 减少。"""
        def handler(event):
            pass

        self.bus.subscribe(handler, event_type=ToolStartedEvent)
        assert self.bus.subscriber_count == 1

        self.bus.unsubscribe(handler, event_type=ToolStartedEvent)
        assert self.bus.subscriber_count == 0

    # ── 发布行为 ────────────────────────────────────────

    def test_publish_multiple_handlers(self):
        """发布事件后所有匹配 handler 被调用。"""
        received1 = []
        received2 = []

        def h1(event):
            received1.append(event)

        def h2(event):
            received2.append(event)

        self.bus.subscribe(h1, event_type=ToolStartedEvent)
        self.bus.subscribe(h2, event_type=ToolStartedEvent)

        self.bus.publish(ToolStartedEvent(label="a"))

        assert len(received1) == 1
        assert len(received2) == 1

    def test_publish_all_and_type_both_deliver(self):
        """全量订阅和按类型订阅都收到匹配事件。"""
        type_received = []
        all_received = []

        def type_handler(event):
            type_received.append(event)

        def all_handler(event):
            all_received.append(event)

        self.bus.subscribe(type_handler, event_type=ToolStartedEvent)
        self.bus.subscribe(all_handler)  # 全量

        self.bus.publish(ToolStartedEvent(label="a"))

        assert len(type_received) == 1
        assert len(all_received) == 1

    def test_publish_no_subscriber(self):
        """无订阅者时发布事件不报错。"""
        self.bus.publish(ToolStartedEvent(label="a"))

    # ── 异常隔离 ────────────────────────────────────────

    def test_handler_exception_does_not_affect_others(self):
        """单个 handler 抛异常不影响其他 handler。"""
        received = []

        def bad_handler(event):
            raise ValueError("模拟异常")

        def good_handler(event):
            received.append(event)

        self.bus.subscribe(bad_handler, event_type=ToolStartedEvent)
        self.bus.subscribe(good_handler, event_type=ToolStartedEvent)

        # 不应抛出异常
        self.bus.publish(ToolStartedEvent(label="a"))

        assert len(received) == 1

    # ── 类型检查 ────────────────────────────────────────

    def test_subscribe_invalid_type_raises(self):
        """传入非 DisplayEvent 子类报 TypeError。"""
        def handler(event):
            pass

        import pytest
        with pytest.raises(TypeError, match="必须是 DisplayEvent"):
            self.bus.subscribe(handler, event_type=str)  # type: ignore

    def test_register_batched_event_invalid_type_raises(self):
        """register_batched_event 传入非 DisplayEvent 子类报 TypeError。"""
        import pytest
        with pytest.raises(TypeError, match="必须是 DisplayEvent"):
            self.bus.register_batched_event(str)  # type: ignore

    def test_unregister_batched_event_invalid_type_raises(self):
        """unregister_batched_event 传入非 DisplayEvent 子类报 TypeError。"""
        import pytest
        with pytest.raises(TypeError, match="必须是 DisplayEvent"):
            self.bus.unregister_batched_event(str)  # type: ignore

    # ── 批处理注册 ──────────────────────────────────────

    def test_register_batched_event_then_publish(self):
        """注册批处理后发布事件仍正常工作（无事件循环时自动降级直发）。"""
        received = []

        def handler(event):
            received.append(event)

        self.bus.subscribe(handler, event_type=ContentChunkEvent)
        self.bus.register_batched_event(ContentChunkEvent)

        # 无运行中事件循环 → _TimeWindowBatcher 自动降级为直接刷新
        self.bus.publish(ContentChunkEvent(text="chunk1"))

        assert len(received) == 1

    def test_unregister_batched_event(self):
        """取消批处理注册后事件正常直发。"""
        received = []

        def handler(event):
            received.append(event)

        self.bus.subscribe(handler, event_type=ContentChunkEvent)
        self.bus.register_batched_event(ContentChunkEvent)
        self.bus.unregister_batched_event(ContentChunkEvent)

        self.bus.publish(ContentChunkEvent(text="chunk1"))

        assert len(received) == 1

    # ── 默认实例 ────────────────────────────────────────

    def test_get_default_returns_singleton(self):
        """get_default() 返回单例。"""
        bus1 = DisplayEventBus.get_default()
        bus2 = DisplayEventBus.get_default()
        assert bus1 is bus2

    def test_reset_default_creates_new_instance(self):
        """reset_default() 后 get_default() 返回新实例。"""
        old = DisplayEventBus.get_default()
        DisplayEventBus.reset_default()
        new = DisplayEventBus.get_default()
        assert old is not new

    def test_source_property(self):
        """publish 时 source 可被覆盖。"""
        received = []

        def handler(event):
            received.append(event)

        self.bus.subscribe(handler, event_type=ToolStartedEvent)
        self.bus.publish(ToolStartedEvent(label="a", source="test-agent"))

        assert received[0].source == "test-agent"

    def test_publish_sets_default_source(self):
        """event 无 source 时使用 bus._source（默认为空）。"""
        received = []

        def handler(event):
            received.append(event)

        self.bus.subscribe(handler, event_type=ToolStartedEvent)
        self.bus.publish(ToolStartedEvent(label="a"))

        assert received[0].source == ""

    def test_default_source_not_overwritten(self):
        """publish 不修改 DisplayEvent 自身的 source 字段（仅影响 CoreEvent 层）。"""
        self.bus._source = "display"
        received = []

        def handler(event):
            received.append(event)

        self.bus.subscribe(handler, event_type=ToolStartedEvent)
        self.bus.publish(ToolStartedEvent(label="a"))

        # DisplayEvent 创建时未设 source，默认保持 ""
        assert received[0].source == ""
