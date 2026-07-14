"""UI 适配器 — 实现 core/ports 接口

桥接 core/ports 定义的抽象端口和 ui/ 包的具体实现。
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Optional

from ..core.ports.events import EventPort
from ..tui.events import publish_output


class UIDisplayAdapter:
    """实现显示端口接口，全部委托给 self._wrapped。

    有真实逻辑的方法（tool_start/tool_done）保留显式定义，
    其余方法通过 __getattr__ 动态转发到 self._wrapped。
    """

    def __init__(self, wrapped_display=None):
        self._wrapped = wrapped_display
        self._tool_names: dict[str, str] = {}

    @property
    def is_web(self) -> bool:
        return getattr(self._wrapped, 'is_web', False)

    def tool_start(self, tool_label, tool_name, detail, metadata=None):
        self._tool_names[tool_label] = tool_name
        if self._wrapped is not None:
            self._wrapped.tool_start(tool_label, tool_name, detail, metadata)

    def tool_done(self, tool_label, tool_name="", success=True, metadata=None):
        if self._wrapped is not None:
            name = tool_name or self._tool_names.pop(tool_label, "")
            self._wrapped.tool_done(label=tool_label, tool_name=name,
                                    success=success, metadata=metadata)

    def __getattr__(self, name: str):
        """动态转发到 self._wrapped。

        捕获显示端口接口中除显式定义方法外的所有方法调用。
        """
        # 防止递归：__getattr__ 在 __init__ 中访问 self._wrapped 时被调用
        if name == '_wrapped':
            raise AttributeError(name)
        wrapped = object.__getattribute__(self, '_wrapped')
        if wrapped is not None:
            attr = getattr(wrapped, name, None)
            if attr is not None:
                return attr
        raise AttributeError(f"'UIDisplayAdapter' has no attribute '{name}'")



class UIEventAdapter(EventPort):
    """实现 EventPort 接口，委托给 ui.events 模块"""

    def __init__(self):
        from ..tui.events.event_bus import DisplayEventBus
        self._bus = DisplayEventBus.get_default()
        self._handlers: dict[str, list] = {}

    def publish(self, event_type, data=None, source="core"):
        from ..tui.events.event_types import OutputEvent, ToolSummaryEvent

        if event_type == "tool_summary" and isinstance(data, dict):
            event = ToolSummaryEvent(
                successful_tools=tuple(data.get("successful_tools", [])),
                failed_tools=tuple(data.get("failed_tools", [])),
                source=source,
            )
            self._bus.publish(event)
        elif event_type == "output" and isinstance(data, dict):
            event = OutputEvent(
                text=data.get("text", ""),
                level=data.get("level", "info"),
                source=source,
            )
            self._bus.publish(event)

    def subscribe(self, event_type, handler):
        # 将字符串 event_type 映射到具体事件类并订阅
        # 实际订阅使用原始 handler，事件总线按事件类型分发
        self._bus.subscribe(handler)
        self._handlers.setdefault(event_type, []).append(handler)

    def unsubscribe(self, event_type, handler):
        self._bus.unsubscribe(handler)
        handlers = self._handlers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)

    def publish_event(self, event, source="core"):
        """发布类型化事件对象 — 直接委托给 DisplayEventBus"""
        if event is not None:
            self._bus.publish(event)

    def subscribe_type(self, event_type, handler):
        """按事件类型订阅"""
        self._bus.subscribe(handler, event_type=event_type)

    def unsubscribe_type(self, event_type, handler):
        """取消按事件类型的订阅"""
        self._bus.unsubscribe(handler, event_type=event_type)


class UIOutputAdapter:
    """默认输出适配器（UI 端），委托给 ui.events.publish_output"""

    def __init__(self):
        self._lock = None

    def _get_lock(self):
        if self._lock is None:
            from ..tui.widgets.lock import render_lock
            self._lock = render_lock
        return self._lock

    def write(self, text, level="info", source="core"):
        publish_output(text, level=level, source=source)

    def write_with_lock(self, text, level="info", source="core"):
        lock = self._get_lock()
        with lock:
            publish_output(text, level=level, source=source)

    @contextmanager
    def locked(self):
        lock = self._get_lock()
        with lock:
            yield
