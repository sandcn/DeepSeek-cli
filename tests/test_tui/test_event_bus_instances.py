"""DisplayEventBus 实例化能力测试 — 架构改进方向 D（2026-08-16）。

背景：DisplayEventBus 原为「进程级强制单例」（``__new__`` 拦截直接构造
返回单例）。方向 D 解除强制单例构造：
  - ``DisplayEventBus()`` 直接构造创建**独立实例**（实例间订阅/发布隔离）；
  - ``get_default()`` 仍返回进程级默认实例（生产路径零变化，CLI/WebUI
    共享默认实例为既有架构约束——webui bridge 依赖默认实例转发事件）；
  - 需要隔离的场景（测试/多会话）经独立实例 + ``emit(event, bus=...)``
    注入打通（配合 B 阶段发布门面）。

本测试锁定：
  1. 直接构造创建独立实例（非单例，互不相等）；
  2. get_default 仍返回单例（多次调用同一实例，reset 后重建）；
  3. 独立实例间订阅完全隔离（A 收不到 B 总线事件）；
  4. 独立实例发布不污染默认实例；
  5. emit 注入独立实例（跨总线发布，配合 B 阶段门面）；
  6. reset_default 不影响已构造的独立实例。
"""

from __future__ import annotations

import pytest

from src.tui.events import DisplayEventBus, emit
from src.tui.events.event_types import OutputEvent, ToolOutputChunkEvent


@pytest.fixture(autouse=True)
def _isolate_event_bus():
    """每个测试隔离默认单例（不污染独立实例测试）。"""
    DisplayEventBus.reset_default()
    yield
    DisplayEventBus.reset_default()


# ═══════════════════════════════════════════════════════════
# 1. 直接构造创建独立实例
# ═══════════════════════════════════════════════════════════

def test_direct_construction_creates_independent_instance():
    """DisplayEventBus() 直接构造创建独立实例（非单例拦截）。"""
    a = DisplayEventBus()
    b = DisplayEventBus()
    assert a is not b
    assert a is not DisplayEventBus.get_default()


def test_get_default_is_still_singleton():
    """get_default() 仍返回进程级默认实例（多次调用同一实例）。"""
    d1 = DisplayEventBus.get_default()
    d2 = DisplayEventBus.get_default()
    assert d1 is d2


def test_get_default_after_reset_rebuilds():
    """reset_default() 后 get_default() 重建新默认实例。"""
    d1 = DisplayEventBus.get_default()
    DisplayEventBus.reset_default()
    d2 = DisplayEventBus.get_default()
    assert d1 is not d2


# ═══════════════════════════════════════════════════════════
# 2. 独立实例间隔离
# ═══════════════════════════════════════════════════════════

def test_independent_instances_isolated():
    """独立实例间订阅完全隔离（A 收不到 B 总线事件）。"""
    bus_a = DisplayEventBus()
    bus_b = DisplayEventBus()
    got_a: list = []
    got_b: list = []
    bus_a.subscribe(lambda e: got_a.append(e), event_type=OutputEvent)
    bus_b.subscribe(lambda e: got_b.append(e), event_type=OutputEvent)

    bus_a.publish(OutputEvent(text="to-a"))
    bus_b.publish(OutputEvent(text="to-b"))

    assert len(got_a) == 1 and got_a[0].text == "to-a"
    assert len(got_b) == 1 and got_b[0].text == "to-b"


def test_independent_publish_does_not_pollute_default():
    """独立实例发布不影响默认实例订阅者。"""
    default_received: list = []
    default_bus = DisplayEventBus.get_default()
    default_bus.subscribe(
        lambda e: default_received.append(e), event_type=OutputEvent,
    )

    isolated = DisplayEventBus()
    isolated.publish(OutputEvent(text="isolated-only"))

    assert default_received == []  # 默认总线未收到独立实例事件


# ═══════════════════════════════════════════════════════════
# 3. emit 注入独立实例（配合 B 阶段发布门面）
# ═══════════════════════════════════════════════════════════

def test_emit_to_independent_bus_cross_instance():
    """emit(event, bus=独立实例) 跨总线发布——默认实例不收到。"""
    default_received: list = []
    isolated_received: list = []
    DisplayEventBus.get_default().subscribe(
        lambda e: default_received.append(e), event_type=ToolOutputChunkEvent,
    )
    isolated = DisplayEventBus()
    isolated.subscribe(
        lambda e: isolated_received.append(e), event_type=ToolOutputChunkEvent,
    )

    emit(
        ToolOutputChunkEvent(label="l", tool_id="t", text="x"),
        bus=isolated,
    )

    assert len(isolated_received) == 1
    assert default_received == []


# ═══════════════════════════════════════════════════════════
# 4. reset_default 不影响独立实例
# ═══════════════════════════════════════════════════════════

def test_reset_default_does_not_affect_independent_instances():
    """reset_default 只重置默认单例缓存——已构造的独立实例订阅保持。"""
    isolated = DisplayEventBus()
    got: list = []
    isolated.subscribe(lambda e: got.append(e), event_type=OutputEvent)

    DisplayEventBus.reset_default()  # 重置默认单例

    isolated.publish(OutputEvent(text="still-alive"))
    assert len(got) == 1  # 独立实例订阅未丢失


# ═══════════════════════════════════════════════════════════
# 5. 多实例并发订阅/发布（线程安全基础）
# ═══════════════════════════════════════════════════════════

def test_independent_instances_concurrent_publish():
    """多独立实例并发发布互不干扰（各自锁保护）。"""
    import threading

    bus_a = DisplayEventBus()
    bus_b = DisplayEventBus()
    got_a: list = []
    got_b: list = []
    bus_a.subscribe(lambda e: got_a.append(e), event_type=OutputEvent)
    bus_b.subscribe(lambda e: got_b.append(e), event_type=OutputEvent)
    errors: list = []

    def _publish(bus, text, sink):
        try:
            for i in range(100):
                bus.publish(OutputEvent(text=f"{text}-{i}"))
            sink.append("done")  # 用哨兵计数，避免与事件列表混淆
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    t1 = threading.Thread(target=_publish, args=(bus_a, "a", []))
    t2 = threading.Thread(target=_publish, args=(bus_b, "b", []))
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert not errors
    assert len(got_a) == 100
    assert len(got_b) == 100
