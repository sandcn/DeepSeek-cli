"""核心事件类型测试 — 覆盖 src/core/events/event_types.py。

验证事件优先级枚举与 CoreEvent 数据类。
"""

from src.core.events.event_types import (
    CoreEvent,
    EventPriority,
    MODEL_CALL_COMPLETED,
    MODEL_CALL_STARTED,
    TOOL_CALL_COMPLETED,
)


def test_event_priority_ordering():
    assert EventPriority.LOWEST < EventPriority.LOW < EventPriority.NORMAL
    assert EventPriority.NORMAL < EventPriority.HIGH < EventPriority.HIGHEST


def test_core_event_defaults():
    ev = CoreEvent(event_type="test.event")
    assert ev.event_type == "test.event"
    assert ev.data == {}
    assert ev.source == "core"
    assert ev.priority is EventPriority.NORMAL


def test_core_event_custom():
    ev = CoreEvent(event_type="x", data={"k": "v"}, priority=EventPriority.HIGH)
    assert ev.data == {"k": "v"}
    assert ev.priority is EventPriority.HIGH


def test_event_type_constants():
    assert MODEL_CALL_STARTED == "model.call.started"
    assert MODEL_CALL_COMPLETED == "model.call.completed"
    assert TOOL_CALL_COMPLETED == "tool.call.completed"
