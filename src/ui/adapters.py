"""UI 适配器 — 实现 core/ports 接口

桥接 core/ports 定义的抽象端口和 ui/ 包的具体实现。
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Optional

from ..core.ports.display import DisplayPort
from ..core.ports.events import EventPort
from ..core.ports.output import OutputPort
from .events import publish_output


class UIDisplayAdapter(DisplayPort):
    """实现 DisplayPort 接口，全部委托给 self._wrapped。"""

    def __init__(self, wrapped_display: Optional[DisplayPort] = None):
        self._wrapped = wrapped_display
        self._tool_names: dict[str, str] = {}

    @property
    def is_web(self) -> bool:
        return getattr(self._wrapped, 'is_web', False)

    # ── DisplayPort 抽象方法实现（委托给 _wrapped） ──

    def tool_start(self, tool_label, tool_name, detail, metadata=None):
        self._tool_names[tool_label] = tool_name
        if self._wrapped is not None:
            self._wrapped.tool_start(tool_label, tool_name, detail, metadata)

    def tool_done(self, tool_label, tool_name="", success=True, metadata=None):
        if self._wrapped is not None:
            name = tool_name or self._tool_names.pop(tool_label, "")
            self._wrapped.tool_done(label=tool_label, tool_name=name,
                                    success=success, metadata=metadata)

    def capture_and_print(self, display_func):
        if self._wrapped is not None:
            return self._wrapped.capture_and_print(display_func)
        return display_func() if callable(display_func) else ""

    def capture_and_print_async(self, display_func):
        if self._wrapped is not None:
            return self._wrapped.capture_and_print_async(display_func)
        return self.capture_and_print(display_func)

    def update_status(self, label: str, status: str) -> None:
        if self._wrapped is not None:
            self._wrapped.update_status(label, status)

    # ── 以下方法满足 ABC 抽象方法要求，委托给 _wrapped ──

    def update_model_phase(self, label, phase, message=""):
        if self._wrapped is not None:
            self._wrapped.update_model_phase(label, phase, message)

    def update_usage(self, label, usage, replace=False):
        if self._wrapped is not None:
            self._wrapped.update_usage(label, usage, replace)

    def update_speed(self, label, speed):
        if self._wrapped is not None:
            self._wrapped.update_speed(label, speed)

    def update_live_input(self, label, tokens):
        if self._wrapped is not None:
            self._wrapped.update_live_input(label, tokens)

    def update_live_output(self, label, tokens):
        if self._wrapped is not None:
            self._wrapped.update_live_output(label, tokens)

    def tool_batch_start(self, label, names):
        if self._wrapped is not None:
            self._wrapped.tool_batch_start(label, names)

    def tool_parsing(self, label, tool_name, arguments=""):
        if self._wrapped is not None:
            self._wrapped.tool_parsing(label, tool_name, arguments)

    def update_parse_info(self, label, tool_name, tokens, elapsed):
        if self._wrapped is not None:
            self._wrapped.update_parse_info(label, tool_name, tokens, elapsed)

    def parse_info_done(self, label):
        if self._wrapped is not None:
            self._wrapped.parse_info_done(label)

    def update_agent_status(self, label, status):
        if self._wrapped is not None:
            self._wrapped.update_agent_status(label, status)

    def add_agent(self, label, description, status="running"):
        if self._wrapped is not None:
            self._wrapped.add_agent(label, description, status)

    def start(self):
        if self._wrapped is not None:
            self._wrapped.start()

    def stop(self):
        if self._wrapped is not None:
            self._wrapped.stop()


class UIEventAdapter(EventPort):
    """实现 EventPort 接口，委托给 ui.events 模块"""

    def __init__(self):
        from .events.event_bus import DisplayEventBus
        self._bus = DisplayEventBus.get_default()
        self._handlers: dict[str, list] = {}

    def publish(self, event_type, data=None, source="core"):
        from .events.event_types import OutputEvent, ToolSummaryEvent

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


class UIOutputAdapter(OutputPort):
    """实现 OutputPort 接口，委托给 ui.events.publish_output"""

    def __init__(self):
        self._lock = None

    def _get_lock(self):
        if self._lock is None:
            from ._lock import output_lock
            self._lock = output_lock
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
