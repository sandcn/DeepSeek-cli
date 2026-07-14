"""测试核心事件系统 CoreEvent / EventPriority / CoreEventBus

覆盖内容：
  1. CoreEvent dataclass（创建/默认值/不可变）
  2. EventPriority 枚举（数值/顺序）
  3. 订阅与发布（基本功能/数据传递/返回计数）
  4. 通配符订阅（"model.*" / "*" / 不匹配）
  5. 优先级排序（高优先级先执行 / 相同优先级按注册顺序）
  6. 取消订阅（成功/失败/取消后不触发）
  7. 异常隔离（异常不传播 / 计数正确）
  8. 统计（get_stats / clear / subscriber_count）
  9. 全局单例（get_default_bus / set_default_bus / reset_default_bus / 多线程）
  10. 线程安全（多线程并发 publish / subscribe）
  11. 事件类型常量（非空 / 数量合理）
  13. 事件对象池（EventPool acquire/release/线程安全）
"""

import threading
import time
from typing import Any

import pytest

from src.core.events import (
    CoreEvent,
    CoreEventBus,
    EventPriority,
    get_default_bus,
    set_default_bus,
    reset_default_bus,
)
from src.tui.events import EventPool
from src.tui.events.event_types import (
    ContentChunkEvent,
    ReasoningChunkEvent,
    ToolOutputChunkEvent,
    ToolParsingEvent,
    ToolStartedEvent,
)
from src.core.events.event_types import (
    MODEL_CALL_STARTED,
    MODEL_CALL_COMPLETED,
    MODEL_CALL_FAILED,
    MODEL_STREAM_CHUNK,
    TOOL_CALL_STARTED,
    TOOL_CALL_COMPLETED,
    TOOL_CALL_FAILED,
    SESSION_STARTED,
    SESSION_COMPLETED,
    SESSION_INTERRUPTED,
    SESSION_SAVED,
    CONTEXT_COMPRESSED,
    CONTEXT_COMPRESS_FAILED,
    CONFIG_CHANGED,
    APP_BOOTSTRAP,
    APP_SHUTDOWN,
)


# ===============================================================
# 1. CoreEvent dataclass
# ===============================================================

class TestCoreEvent:
    """CoreEvent dataclass 的创建、默认值、不可变性"""

    def test_create_with_minimal_args(self):
        """仅传 event_type，其他取默认值"""
        event = CoreEvent(event_type="test.event")
        assert event.event_type == "test.event"
        assert event.data == {}
        assert event.source == "core"
        assert event.timestamp == 0.0
        assert event.priority == EventPriority.NORMAL

    def test_create_with_all_fields(self):
        """传入所有字段"""
        event = CoreEvent(
            event_type="test.all",
            data={"key": "value"},
            source="custom_source",
            timestamp=123.456,
            priority=EventPriority.HIGH,
        )
        assert event.event_type == "test.all"
        assert event.data == {"key": "value"}
        assert event.source == "custom_source"
        assert event.timestamp == 123.456
        assert event.priority == EventPriority.HIGH

    def test_create_with_empty_data(self):
        """显式传空 dict 的 data"""
        event = CoreEvent(event_type="test.empty", data={})
        assert event.data == {}

    def test_create_with_none_data(self):
        """传 None 给 data（用默认工厂）"""
        event = CoreEvent(event_type="test.none", data=None)
        assert event.data is None

    def test_frozen_cannot_modify_event_type(self):
        """frozen=True，修改 event_type 抛 FrozenInstanceError"""
        event = CoreEvent(event_type="test.frozen")
        with pytest.raises(Exception):
            event.event_type = "modified"

    def test_frozen_cannot_modify_data(self):
        """frozen=True，修改 data 抛异常"""
        event = CoreEvent(event_type="test.frozen2")
        with pytest.raises(Exception):
            event.data = {"new": "data"}

    def test_frozen_cannot_modify_source(self):
        """frozen=True，修改 source 抛异常"""
        event = CoreEvent(event_type="test.frozen3")
        with pytest.raises(Exception):
            event.source = "hacker"

    def test_frozen_cannot_modify_timestamp(self):
        """frozen=True，修改 timestamp 抛异常"""
        event = CoreEvent(event_type="test.frozen4")
        with pytest.raises(Exception):
            event.timestamp = 999.0

    def test_frozen_cannot_modify_priority(self):
        """frozen=True，修改 priority 抛异常"""
        event = CoreEvent(event_type="test.frozen5")
        with pytest.raises(Exception):
            event.priority = EventPriority.HIGHEST

    def test_default_timestamp_is_zero(self):
        """timestamp 默认值为 0.0"""
        event = CoreEvent(event_type="test.ts")
        assert event.timestamp == 0.0
        assert isinstance(event.timestamp, float)

    def test_default_priority_is_normal(self):
        """priority 默认值为 EventPriority.NORMAL"""
        event = CoreEvent(event_type="test.pri")
        assert event.priority == EventPriority.NORMAL
        assert event.priority.value == 50

    def test_default_data_is_empty_dict(self):
        """data 默认值为空 dict（通过 field(default_factory=dict)）"""
        event = CoreEvent(event_type="test.data")
        assert event.data == {}
        assert isinstance(event.data, dict)

    def test_data_default_factory_is_independent(self):
        """每次创建的 data 是独立对象"""
        e1 = CoreEvent(event_type="test.indep")
        e2 = CoreEvent(event_type="test.indep")
        e1.data["new"] = "value"
        # e2 不受 e1 影响
        assert "new" not in e2.data

    def test_repr_contains_event_type(self):
        """repr 包含事件类型"""
        event = CoreEvent(event_type="test.repr")
        assert "test.repr" in repr(event)


# ===============================================================
# 2. EventPriority 枚举
# ===============================================================

class TestEventPriority:
    """EventPriority 枚举数值和顺序"""

    def test_lowest_value(self):
        assert EventPriority.LOWEST.value == 0

    def test_low_value(self):
        assert EventPriority.LOW.value == 25

    def test_normal_value(self):
        assert EventPriority.NORMAL.value == 50

    def test_high_value(self):
        assert EventPriority.HIGH.value == 75

    def test_highest_value(self):
        assert EventPriority.HIGHEST.value == 100

    def test_ordering_lowest_lt_low(self):
        assert EventPriority.LOWEST < EventPriority.LOW

    def test_ordering_low_lt_normal(self):
        assert EventPriority.LOW < EventPriority.NORMAL

    def test_ordering_normal_lt_high(self):
        assert EventPriority.NORMAL < EventPriority.HIGH

    def test_ordering_high_lt_highest(self):
        assert EventPriority.HIGH < EventPriority.HIGHEST

    def test_ordering_full_chain(self):
        """完整顺序：LOWEST < LOW < NORMAL < HIGH < HIGHEST"""
        priorities = [
            EventPriority.LOWEST,
            EventPriority.LOW,
            EventPriority.NORMAL,
            EventPriority.HIGH,
            EventPriority.HIGHEST,
        ]
        for i in range(len(priorities) - 1):
            assert priorities[i] < priorities[i + 1]

    def test_is_intenum(self):
        assert issubclass(EventPriority, int)

    def test_from_value(self):
        assert EventPriority(0) == EventPriority.LOWEST
        assert EventPriority(50) == EventPriority.NORMAL
        assert EventPriority(100) == EventPriority.HIGHEST


# ===============================================================
# 3. 订阅与发布
# ===============================================================

class TestSubscribePublish:
    """订阅后发布，处理器被调用"""

    def test_subscribe_and_publish(self):
        bus = CoreEventBus()
        results = []

        def handler(event: CoreEvent):
            results.append(event.event_type)

        bus.subscribe("test.basic", handler)
        count = bus.publish("test.basic")
        assert count == 1
        assert results == ["test.basic"]

    def test_publish_returns_handler_count(self):
        bus = CoreEventBus()
        calls = []

        def h1(event):
            calls.append("h1")

        def h2(event):
            calls.append("h2")

        bus.subscribe("test.count", h1)
        bus.subscribe("test.count", h2)
        count = bus.publish("test.count")
        assert count == 2
        assert len(calls) == 2

    def test_data_passed_to_handler(self):
        bus = CoreEventBus()
        received_data = []

        def handler(event: CoreEvent):
            received_data.append(event.data)

        bus.subscribe("test.data", handler)
        bus.publish("test.data", {"msg": "hello"})
        assert received_data == [{"msg": "hello"}]

    def test_data_defaults_to_empty_dict(self):
        bus = CoreEventBus()
        received_data = []

        def handler(event: CoreEvent):
            received_data.append(event.data)

        bus.subscribe("test.nodata", handler)
        bus.publish("test.nodata")
        assert received_data == [{}]

    def test_source_passed_to_handler(self):
        bus = CoreEventBus()
        received_sources = []

        def handler(event: CoreEvent):
            received_sources.append(event.source)

        bus.subscribe("test.src", handler)
        bus.publish("test.src", source="custom_source")
        assert received_sources == ["custom_source"]

    def test_timestamp_set_on_publish(self):
        bus = CoreEventBus()
        received_ts = []

        def handler(event: CoreEvent):
            received_ts.append(event.timestamp)

        before = time.time()
        bus.subscribe("test.ts", handler)
        bus.publish("test.ts")
        after = time.time()
        assert len(received_ts) == 1
        assert before <= received_ts[0] <= after

    def test_multiple_handlers_same_event(self):
        bus = CoreEventBus()
        order = []

        def h1(event):
            order.append("h1")

        def h2(event):
            order.append("h2")

        bus.subscribe("test.multi", h1, EventPriority.HIGH)
        bus.subscribe("test.multi", h2, EventPriority.LOW)
        bus.publish("test.multi")
        # HIGH 优先级先执行
        assert order == ["h1", "h2"]

    def test_no_subscriber_returns_zero(self):
        bus = CoreEventBus()
        count = bus.publish("test.nobody")
        assert count == 0

    def test_handler_receives_core_event_instance(self):
        bus = CoreEventBus()
        received = []

        def handler(event: CoreEvent):
            received.append(event)

        bus.subscribe("test.instance", handler)
        bus.publish("test.instance", {"a": 1})
        assert len(received) == 1
        assert isinstance(received[0], CoreEvent)
        assert received[0].event_type == "test.instance"
        assert received[0].data == {"a": 1}


# ===============================================================
# 4. 通配符订阅
# ===============================================================

class TestWildcardSubscription:
    """通配符 pattern 匹配"""

    def test_prefix_wildcard_matches(self):
        bus = CoreEventBus()
        results = []

        def handler(event: CoreEvent):
            results.append(event.event_type)

        bus.subscribe("model.*", handler)
        bus.publish("model.call.completed")
        assert results == ["model.call.completed"]

    def test_prefix_wildcard_matches_multiple(self):
        bus = CoreEventBus()
        results = []

        def handler(event: CoreEvent):
            results.append(event.event_type)

        bus.subscribe("model.*", handler)
        bus.publish("model.call.started")
        bus.publish("model.call.completed")
        bus.publish("model.stream.chunk")
        assert results == ["model.call.started", "model.call.completed", "model.stream.chunk"]

    def test_prefix_wildcard_does_not_match_other_prefix(self):
        bus = CoreEventBus()
        results = []

        def handler(event: CoreEvent):
            results.append(event.event_type)

        bus.subscribe("model.*", handler)
        bus.publish("tool.call.started")
        assert results == []

    def test_star_matches_all(self):
        bus = CoreEventBus()
        results = []

        def handler(event: CoreEvent):
            results.append(event.event_type)

        bus.subscribe("*", handler)
        bus.publish("anything.here")
        bus.publish("another.event.type")
        assert results == ["anything.here", "another.event.type"]

    def test_star_matches_even_with_no_dots(self):
        bus = CoreEventBus()
        results = []

        def handler(event: CoreEvent):
            results.append(event.event_type)

        bus.subscribe("*", handler)
        bus.publish("simple")
        assert results == ["simple"]

    def test_non_matching_pattern_not_triggered(self):
        bus = CoreEventBus()
        results = []

        def handler(event: CoreEvent):
            results.append(event.event_type)

        bus.subscribe("session.*", handler)
        bus.publish("model.call.completed")
        assert results == []

    def test_exact_and_wildcard_both_match(self):
        """精确匹配和通配符同时匹配时，handler 只被调用一次（去重）"""
        bus = CoreEventBus()
        results = []

        def handler(event: CoreEvent):
            results.append(event.event_type)

        bus.subscribe("model.call.completed", handler)
        bus.subscribe("model.*", handler)
        bus.publish("model.call.completed")
        assert results == ["model.call.completed"]

    def test_star_and_prefix_wildcard_dedup(self):
        """* 和 model.* 同时匹配时去重"""
        bus = CoreEventBus()
        results = []

        def handler(event: CoreEvent):
            results.append(event.event_type)

        bus.subscribe("*", handler)
        bus.subscribe("model.*", handler)
        bus.publish("model.event")
        assert results == ["model.event"]

    def test_two_different_handlers_both_called(self):
        """通配符和精确匹配各自注册不同 handler，两个都被调用"""
        bus = CoreEventBus()
        r1, r2 = [], []

        def h1(event):
            r1.append("wildcard")

        def h2(event):
            r2.append("exact")

        bus.subscribe("test.*", h1)
        bus.subscribe("test.hello", h2)
        bus.publish("test.hello")
        assert r1 == ["wildcard"]
        assert r2 == ["exact"]

    def test_wildcard_not_match_partial_prefix(self):
        """'model.*' 不匹配 'model'（无点号后缀）"""
        bus = CoreEventBus()
        results = []

        def handler(event: CoreEvent):
            results.append(event.event_type)

        bus.subscribe("model.*", handler)
        bus.publish("model")
        assert results == []

    def test_exact_match_still_works_with_wildcard_present(self):
        bus = CoreEventBus()
        results = []

        def handler(event: CoreEvent):
            results.append(event.event_type)

        bus.subscribe("*", handler)
        bus.subscribe("specific.event", lambda e: results.append("exact:" + e.event_type))
        bus.publish("specific.event")
        assert "specific.event" in results


# ===============================================================
# 5. 优先级排序
# ===============================================================

class TestPriority:
    """优先级高的处理器先被调用"""

    def test_high_priority_before_low(self):
        bus = CoreEventBus()
        order = []

        def low(event):
            order.append("low")

        def high(event):
            order.append("high")

        bus.subscribe("test.pri", low, EventPriority.LOW)
        bus.subscribe("test.pri", high, EventPriority.HIGH)
        bus.publish("test.pri")
        assert order == ["high", "low"]

    def test_highest_before_normal(self):
        bus = CoreEventBus()
        order = []

        def normal_h(event):
            order.append("normal")

        def highest_h(event):
            order.append("highest")

        bus.subscribe("test.pri2", normal_h, EventPriority.NORMAL)
        bus.subscribe("test.pri2", highest_h, EventPriority.HIGHEST)
        bus.publish("test.pri2")
        assert order == ["highest", "normal"]

    def test_same_priority_registration_order(self):
        """相同优先级按注册顺序执行"""
        bus = CoreEventBus()
        order = []

        def first(event):
            order.append("first")

        def second(event):
            order.append("second")

        def third(event):
            order.append("third")

        bus.subscribe("test.same", first, EventPriority.NORMAL)
        bus.subscribe("test.same", second, EventPriority.NORMAL)
        bus.subscribe("test.same", third, EventPriority.NORMAL)
        bus.publish("test.same")
        assert order == ["first", "second", "third"]

    def test_mixed_priorities_full_order(self):
        bus = CoreEventBus()
        order = []

        bus.subscribe("test.mix", lambda e: order.append(100), EventPriority.HIGHEST)
        bus.subscribe("test.mix", lambda e: order.append(75), EventPriority.HIGH)
        bus.subscribe("test.mix", lambda e: order.append(50), EventPriority.NORMAL)
        bus.subscribe("test.mix", lambda e: order.append(25), EventPriority.LOW)
        bus.subscribe("test.mix", lambda e: order.append(0), EventPriority.LOWEST)

        bus.publish("test.mix")
        assert order == [100, 75, 50, 25, 0]

    def test_priorities_across_exact_and_wildcard(self):
        """精确匹配和通配符的 handler 按"精确优先于通配符"合并后各分组内按优先级"""
        bus = CoreEventBus()
        order = []

        bus.subscribe("test.x", lambda e: order.append("exact_low"), EventPriority.LOW)
        bus.subscribe("test.*", lambda e: order.append("wildcard_high"), EventPriority.HIGH)

        bus.publish("test.x")
        # 当前实现：精确匹配 handler 先于通配符 handler（各自组内按优先级排序）
        assert order == ["exact_low", "wildcard_high"]


# ===============================================================
# 6. 取消订阅
# ===============================================================

class TestUnsubscribe:
    """取消订阅行为"""

    def test_unsubscribe_returns_true(self):
        bus = CoreEventBus()
        def handler(event):
            pass
        bus.subscribe("test.uns", handler)
        result = bus.unsubscribe("test.uns", handler)
        assert result is True

    def test_unsubscribe_prevents_handler_call(self):
        bus = CoreEventBus()
        results = []

        def handler(event):
            results.append("called")

        bus.subscribe("test.uns2", handler)
        bus.unsubscribe("test.uns2", handler)
        bus.publish("test.uns2")
        assert results == []

    def test_unsubscribe_nonexistent_handler_returns_false(self):
        bus = CoreEventBus()
        def handler(event):
            pass
        result = bus.unsubscribe("test.nonexist", handler)
        assert result is False

    def test_unsubscribe_nonexistent_event_type_returns_false(self):
        bus = CoreEventBus()
        def handler(event):
            pass
        result = bus.unsubscribe("never.subscribed", handler)
        assert result is False

    def test_unsubscribe_twice_returns_false(self):
        bus = CoreEventBus()
        results = []

        def handler(event):
            results.append("called")

        bus.subscribe("test.double", handler)
        assert bus.unsubscribe("test.double", handler) is True
        assert bus.unsubscribe("test.double", handler) is False

    def test_unsubscribe_one_handler_keeps_others(self):
        bus = CoreEventBus()
        results = []

        def h1(event):
            results.append("h1")

        def h2(event):
            results.append("h2")

        bus.subscribe("test.multi", h1)
        bus.subscribe("test.multi", h2)
        bus.unsubscribe("test.multi", h1)
        bus.publish("test.multi")
        assert results == ["h2"]

    def test_unsubscribe_from_wildcard(self):
        bus = CoreEventBus()
        results = []

        def handler(event):
            results.append(event.event_type)

        bus.subscribe("model.*", handler)
        bus.unsubscribe("model.*", handler)
        bus.publish("model.call.completed")
        assert results == []

    def test_unsubscribe_from_star(self):
        bus = CoreEventBus()
        results = []

        def handler(event):
            results.append(event.event_type)

        bus.subscribe("*", handler)
        bus.unsubscribe("*", handler)
        bus.publish("anything")
        assert results == []


# ===============================================================
# 7. 异常隔离
# ===============================================================

class TestExceptionIsolation:
    """某个处理器抛异常不影响其他处理器"""

    def test_exception_handler_does_not_affect_others(self):
        bus = CoreEventBus()
        results = []

        def bad_handler(event):
            raise ValueError("故意的")

        def good_handler(event):
            results.append("ok")

        bus.subscribe("test.exc", bad_handler)
        bus.subscribe("test.exc", good_handler)
        # 不应向外传播异常
        bus.publish("test.exc")
        assert results == ["ok"]

    def test_publish_count_excludes_exception_handlers(self):
        """异常处理器不计入返回计数"""
        bus = CoreEventBus()

        def bad_handler(event):
            raise ValueError("boom")

        def good_handler(event):
            pass

        bus.subscribe("test.exc_count", bad_handler)
        bus.subscribe("test.exc_count", good_handler)
        count = bus.publish("test.exc_count")
        # 只有 good_handler 成功执行
        assert count == 1

    def test_multiple_exceptions_isolated(self):
        bus = CoreEventBus()
        results = []

        def bad1(event):
            raise RuntimeError("error1")

        def bad2(event):
            raise RuntimeError("error2")

        def good(event):
            results.append("survivor")

        bus.subscribe("test.multi_exc", bad1)
        bus.subscribe("test.multi_exc", bad2)
        bus.subscribe("test.multi_exc", good)
        bus.publish("test.multi_exc")
        assert results == ["survivor"]

    def test_no_exception_no_leak(self):
        """所有处理器正常时 count 等于 handler 数"""
        bus = CoreEventBus()
        calls = []

        def h1(event):
            calls.append(1)

        def h2(event):
            calls.append(2)

        bus.subscribe("test.clean", h1)
        bus.subscribe("test.clean", h2)
        count = bus.publish("test.clean")
        assert count == 2
        assert calls == [1, 2]


# ===============================================================
# 8. 统计
# ===============================================================

class TestStats:
    """get_stats / clear / subscriber_count"""

    def test_get_stats_after_publish(self):
        bus = CoreEventBus()
        bus.publish("test.stat1")
        bus.publish("test.stat1")
        bus.publish("test.stat2")
        stats = bus.get_stats()
        assert stats == {"test.stat1": 2, "test.stat2": 1}

    def test_get_stats_empty_initially(self):
        bus = CoreEventBus()
        assert bus.get_stats() == {}

    def test_clear_resets_stats(self):
        bus = CoreEventBus()
        bus.publish("test.clean")
        bus.publish("test.clean")
        bus.clear()
        assert bus.get_stats() == {}

    def test_clear_also_clears_subscribers(self):
        bus = CoreEventBus()
        bus.subscribe("test.sub", lambda e: None)
        assert bus.subscriber_count() == 1
        bus.clear()
        assert bus.subscriber_count() == 0

    def test_subscriber_count_with_event_type(self):
        bus = CoreEventBus()
        bus.subscribe("test.a", lambda e: None)
        bus.subscribe("test.a", lambda e: None)
        bus.subscribe("test.b", lambda e: None)
        assert bus.subscriber_count("test.a") == 2
        assert bus.subscriber_count("test.b") == 1

    def test_subscriber_count_total(self):
        bus = CoreEventBus()
        assert bus.subscriber_count() == 0
        bus.subscribe("evt1", lambda e: None)
        bus.subscribe("evt1", lambda e: None)
        bus.subscribe("evt2", lambda e: None)
        assert bus.subscriber_count() == 3

    def test_subscriber_count_nonexistent_event(self):
        bus = CoreEventBus()
        assert bus.subscriber_count("nonexistent") == 0

    def test_stats_isolated_per_bus_instance(self):
        bus1 = CoreEventBus()
        bus2 = CoreEventBus()
        bus1.publish("only.bus1")
        assert bus1.get_stats() == {"only.bus1": 1}
        assert bus2.get_stats() == {}


# ===============================================================
# 9. 全局单例
# ===============================================================

class TestDefaultBus:
    """get_default_bus / set_default_bus / reset_default_bus"""

    def setup_method(self):
        """每个测试前重置默认总线"""
        reset_default_bus()

    def test_get_default_bus_returns_instance(self):
        bus = get_default_bus()
        assert isinstance(bus, CoreEventBus)

    def test_multiple_calls_return_same_instance(self):
        bus1 = get_default_bus()
        bus2 = get_default_bus()
        assert bus1 is bus2

    def test_set_default_bus_injects_instance(self):
        custom_bus = CoreEventBus()
        set_default_bus(custom_bus)
        assert get_default_bus() is custom_bus

    def test_reset_default_bus_clears(self):
        bus_before = get_default_bus()
        reset_default_bus()
        bus_after = get_default_bus()
        assert bus_after is not bus_before

    def test_set_then_reset(self):
        custom = CoreEventBus()
        set_default_bus(custom)
        assert get_default_bus() is custom
        reset_default_bus()
        assert get_default_bus() is not custom

    def test_thread_safety_multiple_threads_get_default_bus(self):
        """多线程并发获取默认总线不会崩溃"""
        buses = []
        errors = []

        def get_bus():
            try:
                bus = get_default_bus()
                buses.append(bus)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=get_bus) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        # 所有线程拿到同一个实例
        assert all(b is buses[0] for b in buses)

    def test_default_bus_usable_for_pub_sub(self):
        """默认总线可以正常发布订阅"""
        bus = get_default_bus()
        results = []

        def handler(event):
            results.append(event.event_type)

        bus.subscribe("default.test", handler)
        bus.publish("default.test")
        assert results == ["default.test"]


# ===============================================================
# 10. 线程安全
# ===============================================================

class TestThreadSafety:
    """多线程并发 publish / subscribe 不会崩溃"""

    def test_concurrent_publish(self):
        """多线程并发发布同一事件不崩溃"""
        bus = CoreEventBus()
        results = []
        lock = threading.Lock()

        def handler(event):
            with lock:
                results.append(event.event_type)

        bus.subscribe("concurrent.pub", handler)

        def publish_thread():
            for _ in range(50):
                bus.publish("concurrent.pub")

        threads = [threading.Thread(target=publish_thread) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(results) == 250  # 5 线程 * 50 次

    def test_concurrent_subscribe(self):
        """多线程并发订阅不崩溃"""
        bus = CoreEventBus()
        errors = []

        def subscribe_thread():
            try:
                for i in range(20):
                    bus.subscribe(f"concurrent.sub.{i}", lambda e, idx=i: None)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=subscribe_thread) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(errors) == 0
        assert bus.subscriber_count() == 100  # 5 * 20

    def test_concurrent_publish_and_subscribe(self):
        """publish 和 subscribe 同时进行不崩溃"""
        bus = CoreEventBus()
        errors = []

        def publisher():
            try:
                for _ in range(100):
                    bus.publish("concurrent.mix")
            except Exception as e:
                errors.append(e)

        def subscriber():
            try:
                for i in range(50):
                    bus.subscribe(f"concurrent.mix", lambda e: None)
            except Exception as e:
                errors.append(e)

        threads = []
        threads.extend([threading.Thread(target=publisher) for _ in range(3)])
        threads.extend([threading.Thread(target=subscriber) for _ in range(3)])

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert len(errors) == 0

    def test_concurrent_unsubscribe(self):
        """多线程并发取消订阅不崩溃"""
        bus = CoreEventBus()
        handlers = []

        for i in range(50):
            def make_handler(idx):
                def h(event):
                    pass
                return h
            h = make_handler(i)
            handlers.append(h)
            bus.subscribe("concurrent.uns", h)

        errors = []

        def unsub_thread(start, end):
            try:
                for i in range(start, end):
                    bus.unsubscribe("concurrent.uns", handlers[i])
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=unsub_thread, args=(0, 25)),
            threading.Thread(target=unsub_thread, args=(25, 50)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(errors) == 0
        assert bus.subscriber_count("concurrent.uns") == 0


# ===============================================================
# 11. 事件类型常量
# ===============================================================

class TestEventTypeConstants:
    """事件类型常量值不为空字符串，数量合理"""

    ALL_CONSTANTS = [
        MODEL_CALL_STARTED,
        MODEL_CALL_COMPLETED,
        MODEL_CALL_FAILED,
        MODEL_STREAM_CHUNK,
        TOOL_CALL_STARTED,
        TOOL_CALL_COMPLETED,
        TOOL_CALL_FAILED,
        SESSION_STARTED,
        SESSION_COMPLETED,
        SESSION_INTERRUPTED,
        SESSION_SAVED,
        CONTEXT_COMPRESSED,
        CONTEXT_COMPRESS_FAILED,
        CONFIG_CHANGED,
        APP_BOOTSTRAP,
        APP_SHUTDOWN,
    ]

    def test_none_is_empty_string(self):
        """所有事件类型常量不为空字符串"""
        for const in self.ALL_CONSTANTS:
            assert const != "", f"常量值为空字符串"
            assert isinstance(const, str), f"常量不是字符串类型: {const}"

    def test_all_start_with_category(self):
        """所有常量以分类前缀开头"""
        for const in self.ALL_CONSTANTS:
            assert "." in const, f"常量缺少分类前缀: {const}"

    def test_constant_count_reasonable(self):
        """常量数量合理（至少 10 个）"""
        assert len(self.ALL_CONSTANTS) >= 10

    def test_model_call_constants(self):
        assert MODEL_CALL_STARTED == "model.call.started"
        assert MODEL_CALL_COMPLETED == "model.call.completed"
        assert MODEL_CALL_FAILED == "model.call.failed"
        assert MODEL_STREAM_CHUNK == "model.stream.chunk"

    def test_tool_call_constants(self):
        assert TOOL_CALL_STARTED == "tool.call.started"
        assert TOOL_CALL_COMPLETED == "tool.call.completed"
        assert TOOL_CALL_FAILED == "tool.call.failed"

    def test_session_constants(self):
        assert SESSION_STARTED == "session.started"
        assert SESSION_COMPLETED == "session.completed"
        assert SESSION_INTERRUPTED == "session.interrupted"
        assert SESSION_SAVED == "session.saved"

    def test_context_constants(self):
        assert CONTEXT_COMPRESSED == "context.compressed"
        assert CONTEXT_COMPRESS_FAILED == "context.compress.failed"

    def test_config_constant(self):
        assert CONFIG_CHANGED == "config.changed"

    def test_app_constants(self):
        assert APP_BOOTSTRAP == "app.bootstrap"
        assert APP_SHUTDOWN == "app.shutdown"


# ===============================================================
# 12. 边界条件与清理
# ===============================================================

class TestBusClear:
    """clear 方法重置总线状态"""

    def test_clear_removes_all_subscribers(self):
        bus = CoreEventBus()
        bus.subscribe("test.c1", lambda e: None)
        bus.subscribe("test.c2", lambda e: None)
        assert bus.subscriber_count() == 2
        bus.clear()
        assert bus.subscriber_count() == 0

    def test_clear_resets_stats(self):
        bus = CoreEventBus()
        bus.publish("test.s1")
        bus.publish("test.s2")
        assert bus.get_stats() != {}
        bus.clear()
        assert bus.get_stats() == {}

    def test_after_clear_publish_still_works(self):
        bus = CoreEventBus()
        results = []

        def handler(event):
            results.append(event.event_type)

        bus.subscribe("test.after", handler)
        bus.clear()

        # 重新订阅后应正常
        bus.subscribe("test.after", handler)
        bus.publish("test.after")
        assert results == ["test.after"]


# ===============================================================
# 13. 事件对象池
# ===============================================================

class TestEventPool:
    """EventPool 高频事件对象池的 acquire / release 行为"""

    def test_acquire_release_same_object_returned(self):
        """acquire 后 release，再次 acquire 返回同一对象"""
        pool = EventPool(maxsize=256)
        e1 = pool.acquire(ContentChunkEvent, text="hello", label="agent-1")
        e1_id = id(e1)
        pool.release(e1)
        e2 = pool.acquire(ContentChunkEvent, text="world", label="agent-1")
        assert id(e2) == e1_id, "release 后 acquire 应返回同一对象"

    def test_acquire_release_reasoning(self):
        """ReasoningChunkEvent 同样可复用"""
        pool = EventPool(maxsize=256)
        e1 = pool.acquire(ReasoningChunkEvent, text="思考中...", label="agent-1")
        e1_id = id(e1)
        pool.release(e1)
        e2 = pool.acquire(ReasoningChunkEvent, text="继续思考", label="agent-2")
        assert id(e2) == e1_id

    def test_acquire_release_tool_output(self):
        """ToolOutputChunkEvent 同样可复用"""
        pool = EventPool(maxsize=256)
        e1 = pool.acquire(ToolOutputChunkEvent, text="stdout output", label="call_xxx", source="agent")
        e1_id = id(e1)
        pool.release(e1)
        e2 = pool.acquire(ToolOutputChunkEvent, text="stderr output", label="call_yyy")
        assert id(e2) == e1_id

    def test_acquire_new_when_pool_empty(self):
        """池空时 acquire 创建新对象"""
        pool = EventPool(maxsize=256)
        e1 = pool.acquire(ContentChunkEvent, text="first", label="a")
        e2 = pool.acquire(ContentChunkEvent, text="second", label="b")
        assert e1 is not e2
        assert e1.text == "first"
        assert e2.text == "second"

    def test_field_values_set_correctly(self):
        """acquire 时传入的 kwargs 正确设置到事件字段"""
        pool = EventPool(maxsize=256)
        event = pool.acquire(ContentChunkEvent, text="测试文本", label="agent-1", source="test")
        assert event.text == "测试文本"
        assert event.label == "agent-1"
        assert event.source == "test"
        assert isinstance(event.timestamp, float)
        assert event.timestamp > 0

    def test_timestamp_auto_set(self):
        """timestamp 未传入时自动设为当前时间"""
        pool = EventPool(maxsize=256)
        before = time.time()
        event = pool.acquire(ContentChunkEvent, text="hello", label="a")
        after = time.time()
        assert before <= event.timestamp <= after

    def test_release_resets_fields(self):
        """release 后对象的字段被重置为默认值"""
        pool = EventPool(maxsize=256)
        event = pool.acquire(ContentChunkEvent, text="hello", label="agent-1", source="test")
        pool.release(event)
        # 重新 acquire 并检查字段是新值
        event2 = pool.acquire(ContentChunkEvent, text="new", label="agent-2")
        assert event2.text == "new"
        assert event2.label == "agent-2"
        # 默认值 source 应被重置为空
        assert event2.source == ""

    def test_qsize_after_acquire(self):
        """acquire 后 qsize 减少"""
        pool = EventPool(maxsize=256)
        e1 = pool.acquire(ContentChunkEvent, text="a", label="a1")
        e2 = pool.acquire(ContentChunkEvent, text="b", label="a2")
        assert pool.qsize(ContentChunkEvent) == 0  # 池中无空闲对象
        pool.release(e1)
        assert pool.qsize(ContentChunkEvent) == 1
        pool.release(e2)
        assert pool.qsize(ContentChunkEvent) == 2

    def test_qsize_total(self):
        """qsize 不传参数时返回所有类型的总数"""
        pool = EventPool(maxsize=256)
        c = pool.acquire(ContentChunkEvent, text="c", label="a")
        r = pool.acquire(ReasoningChunkEvent, text="r", label="b")
        t = pool.acquire(ToolOutputChunkEvent, text="t", label="c")
        pool.release(c)
        pool.release(r)
        pool.release(t)
        assert pool.qsize() == 3
        assert pool.qsize(ContentChunkEvent) == 1
        assert pool.qsize(ReasoningChunkEvent) == 1
        assert pool.qsize(ToolOutputChunkEvent) == 1

    def test_maxsize_respected(self):
        """超过 maxsize 的 release 静默丢弃"""
        pool = EventPool(maxsize=2)
        events = []
        for i in range(5):
            e = pool.acquire(ContentChunkEvent, text=str(i), label="a")
            events.append(e)
        # 全部 release 后，池中最多保留 2 个
        for e in events:
            pool.release(e)
        assert pool.qsize(ContentChunkEvent) == 2

    def test_clear_empties_pool(self):
        """clear 清空对象池"""
        pool = EventPool(maxsize=256)
        e1 = pool.acquire(ContentChunkEvent, text="a", label="a1")
        e2 = pool.acquire(ReasoningChunkEvent, text="b", label="a2")
        pool.release(e1)
        pool.release(e2)
        assert pool.qsize() == 2
        pool.clear(ContentChunkEvent)
        assert pool.qsize(ContentChunkEvent) == 0
        assert pool.qsize(ReasoningChunkEvent) == 1
        pool.clear()
        assert pool.qsize() == 0

    def test_acquire_non_high_freq_type(self):
        """非高频事件类型直接创建新对象（不池化）"""
        pool = EventPool(maxsize=256)
        event = pool.acquire(ToolStartedEvent, label="call_xxx", tool_name="read_file")
        assert isinstance(event, ToolStartedEvent)
        assert event.tool_name == "read_file"
        # 非高频类型 release 应无效果（不池化）
        e_id = id(event)
        pool.release(event)
        event2 = pool.acquire(ToolStartedEvent, label="call_yyy", tool_name="write_file")
        # 非高频类型不池化，每次 acquire 都创建新对象
        assert id(event2) != e_id

    def test_release_non_high_freq_no_error(self):
        """非高频事件 release 不抛出异常"""
        pool = EventPool(maxsize=256)
        event = ToolParsingEvent(label="test", tool_name="read_file")
        pool.release(event)  # 不应抛出异常

    def test_acquire_type_error_with_invalid_type(self):
        """传入非 DisplayEvent 子类应抛出 TypeError"""
        pool = EventPool(maxsize=256)
        with pytest.raises(TypeError):
            # 传入非 DisplayEvent 子类（str 不是有效的类型）
            pool.acquire(str, text="hello", label="a")

    def test_maxsize_invalid(self):
        """maxsize < 1 应抛出 ValueError"""
        with pytest.raises(ValueError):
            EventPool(maxsize=0)
        with pytest.raises(ValueError):
            EventPool(maxsize=-1)

    def test_acquire_fields_independent(self):
        """两次 acquire 返回的对象字段互不影响"""
        pool = EventPool(maxsize=256)
        e1 = pool.acquire(ContentChunkEvent, text="alpha", label="agent-1")
        pool.release(e1)
        e2 = pool.acquire(ContentChunkEvent, text="beta", label="agent-2")
        assert e2.text == "beta"
        assert e2.label == "agent-2"

    def test_thread_safety(self):
        """多线程并发 acquire/release 不崩溃"""
        pool = EventPool(maxsize=256)
        errors = []
        N = 200

        def worker():
            try:
                for _ in range(N):
                    e = pool.acquire(ContentChunkEvent, text="thread", label="t")
                    pool.release(e)
            except Exception as ex:
                errors.append(ex)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert len(errors) == 0, f"线程安全测试失败: {errors}"
        # 池中对象数不应超过 maxsize
        assert pool.qsize(ContentChunkEvent) <= pool.maxsize

    def test_thread_safety_different_types(self):
        """多线程并发操作不同类型事件"""
        pool = EventPool(maxsize=256)
        errors = []
        N = 100

        def worker_c():
            try:
                for _ in range(N):
                    e = pool.acquire(ContentChunkEvent, text="c", label="c")
                    pool.release(e)
            except Exception as ex:
                errors.append(ex)

        def worker_r():
            try:
                for _ in range(N):
                    e = pool.acquire(ReasoningChunkEvent, text="r", label="r")
                    pool.release(e)
            except Exception as ex:
                errors.append(ex)

        def worker_t():
            try:
                for _ in range(N):
                    e = pool.acquire(ToolOutputChunkEvent, text="t", label="t")
                    pool.release(e)
            except Exception as ex:
                errors.append(ex)

        threads = [
            threading.Thread(target=worker_c),
            threading.Thread(target=worker_r),
            threading.Thread(target=worker_t),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert len(errors) == 0
        # 每种类型的池独立计数
        assert pool.qsize(ContentChunkEvent) <= pool.maxsize
        assert pool.qsize(ReasoningChunkEvent) <= pool.maxsize
        assert pool.qsize(ToolOutputChunkEvent) <= pool.maxsize


# ===============================================================
# 14. DisplayEventBusAdapter.subscribe() 修复
# ===============================================================

class TestDisplayEventBusAdapterSubscribe:
    """DisplayEventBusAdapter.subscribe() 按 event_type 字符串过滤事件"""

    def setup_method(self):
        """每个测试前重置默认 DisplayEventBus，避免测试间干扰"""
        from src.tui.events.event_bus import DisplayEventBus
        DisplayEventBus.reset_default()

    def teardown_method(self):
        """每个测试后清理"""
        from src.tui.events.event_bus import DisplayEventBus
        DisplayEventBus.reset_default()

    def test_subscribe_with_event_type_filters_by_class_name(self):
        """subscribe("OutputEvent", handler) 仅接收 OutputEvent 事件"""
        from src.core.adapters.events import DisplayEventBusAdapter

        adapter = DisplayEventBusAdapter(source="test")
        results = []

        def handler(event):
            results.append(type(event).__name__)

        # 订阅 OutputEvent 类型
        adapter.subscribe("OutputEvent", handler)

        # 发布 OutputEvent → 应触发 handler
        adapter.publish("output", {"text": "hello", "level": "info", "source": "test"})
        assert len(results) == 1
        assert results[0] == "OutputEvent"

    def test_subscribe_with_event_type_excludes_other_types(self):
        """subscribe("OutputEvent", handler) 不接收 ToolSummaryEvent"""
        from src.core.adapters.events import DisplayEventBusAdapter

        adapter = DisplayEventBusAdapter(source="test")
        results = []

        def handler(event):
            results.append(type(event).__name__)

        # 订阅 OutputEvent 类型
        adapter.subscribe("OutputEvent", handler)

        # 发布 ToolSummaryEvent → 不应触发 handler
        adapter.publish("tool_summary", data={
            "successful_tools": ["read_file"],
            "failed_tools": [],
        })
        assert len(results) == 0

    def test_subscribe_with_none_receives_all_events(self):
        """subscribe(None, handler) 接收所有事件（向后兼容）"""
        from src.core.adapters.events import DisplayEventBusAdapter

        adapter = DisplayEventBusAdapter(source="test")
        results = []

        def handler(event):
            results.append(type(event).__name__)

        # 订阅所有事件
        adapter.subscribe(None, handler)

        # 发布两种事件
        adapter.publish("output", {"text": "hello", "level": "info", "source": "test"})
        adapter.publish("tool_summary", data={
            "successful_tools": ["read_file"],
            "failed_tools": [],
        })

        assert len(results) == 2

    def test_subscribe_with_empty_string_receives_all_events(self):
        """subscribe("", handler) 接收所有事件（向后兼容）"""
        from src.core.adapters.events import DisplayEventBusAdapter

        adapter = DisplayEventBusAdapter(source="test")
        results = []

        def handler(event):
            results.append(type(event).__name__)

        adapter.subscribe("", handler)

        adapter.publish("output", {"text": "hello", "level": "info", "source": "test"})
        adapter.publish("tool_summary", data={
            "successful_tools": ["read_file"],
            "failed_tools": [],
        })

        assert len(results) == 2

    def test_unsubscribe_removes_filtered_subscription(self):
        """unsubscribe 取消过滤订阅后事件不再触发"""
        from src.core.adapters.events import DisplayEventBusAdapter

        adapter = DisplayEventBusAdapter(source="test")
        results = []

        def handler(event):
            results.append(type(event).__name__)

        adapter.subscribe("OutputEvent", handler)
        adapter.publish("output", {"text": "a", "level": "info", "source": "test"})
        assert len(results) == 1

        adapter.unsubscribe("OutputEvent", handler)
        adapter.publish("output", {"text": "b", "level": "info", "source": "test"})
        # 取消订阅后不应再触发
        assert len(results) == 1

    def test_unsubscribe_preserves_other_handlers(self):
        """取消一个 handler 的过滤订阅不影响其他 handler"""
        from src.core.adapters.events import DisplayEventBusAdapter

        adapter = DisplayEventBusAdapter(source="test")
        results1 = []
        results2 = []

        def handler1(event):
            results1.append(type(event).__name__)

        def handler2(event):
            results2.append(type(event).__name__)

        adapter.subscribe("OutputEvent", handler1)
        adapter.subscribe("OutputEvent", handler2)

        adapter.publish("output", {"text": "a", "level": "info", "source": "test"})
        assert len(results1) == 1
        assert len(results2) == 1

        # 取消 handler1
        adapter.unsubscribe("OutputEvent", handler1)

        adapter.publish("output", {"text": "b", "level": "info", "source": "test"})
        assert len(results1) == 1  # handler1 不再触发
        assert len(results2) == 2  # handler2 继续触发

    def test_multiple_event_types_independent(self):
        """多个不同 event_type 的订阅互不干扰"""
        from src.core.adapters.events import DisplayEventBusAdapter

        adapter = DisplayEventBusAdapter(source="test")
        output_results = []
        summary_results = []

        def output_handler(event):
            output_results.append(type(event).__name__)

        def summary_handler(event):
            summary_results.append(type(event).__name__)

        adapter.subscribe("OutputEvent", output_handler)
        adapter.subscribe("ToolSummaryEvent", summary_handler)

        # 发布 OutputEvent
        adapter.publish("output", {"text": "hello", "level": "info", "source": "test"})
        assert len(output_results) == 1
        assert len(summary_results) == 0

        # 发布 ToolSummaryEvent
        adapter.publish("tool_summary", data={
            "successful_tools": ["search"],
            "failed_tools": [],
        })
        assert len(output_results) == 1
        assert len(summary_results) == 1
