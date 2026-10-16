"""ChatUIConsumer 引用计数回归（P0：移除恒真的 ``assert``）。

★ 修复（P0）：``_unregister_consumer`` 中 ``assert _active_consumer_refcount >= 0``
  恒为真（递减已由 ``max(0, ...)`` 钳制），且在 ``python -O`` 下被剥离——
  移除该断言，改为注释说明下限来源；行为不变（计数恒非负、可重复注销）。
"""

from __future__ import annotations

from weakref import WeakValueDictionary

import pytest

from src.tui.state import consumer_registry as reg


class _FakeConsumer:
    """最小 ChatUIConsumer 替身（仅需 ``_started`` 与可弱引用）。"""

    def __init__(self, started: bool = True):
        self._started = started


@pytest.fixture(autouse=True)
def _reset_registry(monkeypatch):
    monkeypatch.setattr(reg, "_active_consumer", None)
    monkeypatch.setattr(reg, "_active_consumer_refcount", 0)
    monkeypatch.setattr(reg, "_active_consumer_stack", [])
    monkeypatch.setattr(reg, "_weak_consumer_registry", WeakValueDictionary())
    yield


def test_register_increments_refcount():
    a = _FakeConsumer()
    reg._register_consumer(a)
    assert reg._active_consumer_refcount == 1
    assert reg.get_active_chat_ui() is a


def test_unregister_never_goes_negative():
    a = _FakeConsumer()
    reg._register_consumer(a)
    for _ in range(5):
        reg._unregister_consumer()
    assert reg._active_consumer_refcount == 0
    assert reg._active_consumer is None


def test_unregister_on_empty_state_is_safe():
    reg._unregister_consumer()
    assert reg._active_consumer_refcount == 0
    assert reg._active_consumer is None


def _stop(consumer: "_FakeConsumer") -> None:
    """模拟 ``ChatUIConsumer.stop()`` 的真实顺序：先停生命周期再注销注册表。"""
    consumer._started = False
    reg._unregister_consumer()


def test_multi_instance_rollback_to_previous():
    """B.stop 后活跃实例回滚到仍启动的 A（而非已停止的 B）。"""
    a = _FakeConsumer()
    b = _FakeConsumer()
    reg._register_consumer(a)
    reg._register_consumer(b)
    assert reg.get_active_chat_ui() is b
    _stop(b)
    assert reg._active_consumer_refcount == 1
    assert reg.get_active_chat_ui() is a


def test_stopped_instance_skipped_in_rollback():
    """A 已停止（_started=False）时，回滚跳过 A。"""
    a = _FakeConsumer(started=False)
    b = _FakeConsumer()
    reg._register_consumer(a)
    reg._register_consumer(b)
    reg._unregister_consumer()
    reg._unregister_consumer()
    assert reg._active_consumer_refcount == 0
    assert reg._active_consumer is None


def test_weak_registry_used_when_active_cleared():
    """_active_consumer 悬空时经弱表兜底恢复同线程实例。"""
    a = _FakeConsumer()
    reg._register_consumer(a)
    reg._active_consumer = None
    assert reg.get_active_chat_ui() is a
