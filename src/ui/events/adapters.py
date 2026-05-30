"""事件总线适配器 — 桥接 DisplayEventBus ↔ BaseDisplay

提供两种适配模式：

1. DisplayEventAdapter（事件→BaseDisplay）
   订阅 EventBus 上的所有事件，转换为对 BaseDisplay 实现的方法调用。
   用于让现有 BaseDisplay 实现（ToolExecutionDisplay, ParallelDisplay）通过 EventBus 驱动。

2. EventBusDisplayProxy（主动发布）
   实现 BaseDisplay 接口，将所有方法调用转为 EventBus 事件发布。
   用于让现有生产者（agent, subagent）通过 BaseDisplay 接口发布事件到 EventBus，
   无需修改生产者代码。

典型用法：
    # 场景A：让 BaseDisplay 成为 EventBus 的消费者
    adapter = DisplayEventAdapter(display_instance)
    adapter.subscribe_to(event_bus)  # adapter 订阅所有事件

    # 场景B：让现有代码通过 BaseDisplay 接口发布事件到 EventBus
    display = EventBusDisplayProxy(event_bus, source="agent")
    agent.display = display  # agent 调用 display.tool_start() → 发布 ToolStartedEvent
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set, Type

from ..base_display import BaseDisplay as _BaseDisplay
from .event_bus import DisplayEventBus, EventHandler
from .event_types import (
    DisplayEvent,
    SessionStarted, SessionStopped,
    ToolParsingEvent, ToolStartedEvent, ToolDoneEvent, ToolBatchStartedEvent,
    AgentAddedEvent, AgentStatusChanged,
    ModelPhaseEvent, UsageUpdatedEvent,
    ParseInfoEvent, ParseInfoDoneEvent, TokenEvent, LiveOutputEvent, LiveInputEvent, SpeedUpdatedEvent,
    OutputEvent, ToolSummaryEvent,
)

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# 模式A：事件 → BaseDisplay
# ═══════════════════════════════════════════════════════════

class DisplayEventAdapter:
    """事件总线适配器 — 将 DisplayEvent 转为 BaseDisplay 方法调用。

    订阅 EventBus，收到事件后调用内部 BaseDisplay 实例的对应方法。
    支持过滤：可通过 `include_types` 限制仅处理特定事件类型。

    用法：
        adapter = DisplayEventAdapter(display)
        adapter.subscribe_to(event_bus)
        # 之后 event_bus.publish(ToolStartedEvent(...)) 会触发 display.tool_start(...)
    """

    # 事件类型 → BaseDisplay 方法名映射表
    _EVENT_METHOD_MAP: Dict[Type[DisplayEvent], str] = {
        ToolParsingEvent: "tool_parsing",
        ToolStartedEvent: "tool_start",
        ToolDoneEvent: "tool_done",
        ModelPhaseEvent: "update_model_phase",
        UsageUpdatedEvent: "update_usage",
        AgentStatusChanged: "update_status",
        SessionStarted: "start",
        SessionStopped: "stop",
    }

    def __init__(
        self,
        display: Any,  # 鸭类型：通过 hasattr/getattr 调用方法，不要求特定基类
        include_types: Optional[Set[Type[DisplayEvent]]] = None,
    ):
        """
        Args:
            display: 任意对象，提供 tool_start/tool_done 等方法（鸭类型接口）
            include_types: 要处理的事件类型集合。None 表示处理所有可映射事件。
        """
        self._display = display
        self._include_types = include_types
        self._handlers: dict[Type[DisplayEvent], EventHandler] = {}
        self._event_bus: DisplayEventBus | None = None

    def subscribe_to(self, event_bus: DisplayEventBus) -> None:
        """订阅 EventBus 上的所有可映射事件。

        先取消所有旧订阅（防止多次调用导致 handler 堆积），
        然后重新订阅所有事件。
        """
        self.unsubscribe_all()
        self._event_bus = event_bus
        for event_type, method_name in self._EVENT_METHOD_MAP.items():
            if self._include_types is not None and event_type not in self._include_types:
                continue
            if hasattr(self._display, method_name):
                # 为每个事件类型创建绑定的 handler
                handler = self._make_handler(event_type, method_name)
                self._handlers[event_type] = handler
                event_bus.subscribe(handler, event_type=event_type)

    def unsubscribe_all(self) -> None:
        """取消所有已订阅的事件 handler。"""
        if self._event_bus is None:
            self._handlers.clear()
            return
        for event_type, handler in self._handlers.items():
            try:
                self._event_bus.unsubscribe(handler, event_type=event_type)
            except Exception:
                _logger.debug("取消订阅 %s 失败（可能已解除绑定）", event_type.__name__)
        self._handlers.clear()

    def _make_handler(self, event_type: Type[DisplayEvent], method_name: str) -> EventHandler:
        """创建将特定事件类型转发到 BaseDisplay 方法的处理函数。"""
        def handler(event: DisplayEvent) -> None:
            method = getattr(self._display, method_name, None)
            if method is None:
                return
            try:
                if isinstance(event, ToolParsingEvent):
                    method(event.label, event.tool_name, event.arguments)
                elif isinstance(event, ToolStartedEvent):
                    method(event.label, event.tool_name, event.detail, event.metadata)
                elif isinstance(event, ToolDoneEvent):
                    method(event.label, event.tool_name, event.success, event.metadata)
                elif isinstance(event, ModelPhaseEvent):
                    method(event.label, event.phase, event.info)
                elif isinstance(event, UsageUpdatedEvent):
                    method(event.label, event.usage, event.replace)
                elif isinstance(event, SessionStarted):
                    method()
                elif isinstance(event, SessionStopped):
                    method(final=event.final)
                elif isinstance(event, AgentStatusChanged):
                    method(event.label, event.status)
            except Exception:
                _logger.exception(
                    "适配器转发 %s → %s() 失败", type(event).__name__, method_name
                )
        return handler


# ═══════════════════════════════════════════════════════════
# 模式B：BaseDisplay 接口 → 事件发布
# ═══════════════════════════════════════════════════════════

class EventBusDisplayProxy(_BaseDisplay):
    """BaseDisplay 代理实现 — 将所有方法调用转为 EventBus 事件发布。

    实现完整的 BaseDisplay 抽象接口，每个方法对应发布一个事件。
    让现有生产者代码（agent, subagent）无需修改即可接入 EventBus：
    只需将 agent.display = EventBusDisplayProxy(event_bus, source="agent")。

    线程安全（事件本身是 frozen dataclass，EventBus.publish 内部有锁）。
    """

    def __init__(
        self,
        event_bus: Optional[DisplayEventBus] = None,
        source: str = "",
        output_target=None,
    ):
        """
        Args:
            event_bus: 要发布事件到的事件总线。None 则使用默认单例。
            source: 事件来源标识，用于事件溯源
            output_target: 可选的输出目标（透传给 BaseDisplay）
        """
        super().__init__(output_target=output_target)
        self._bus = event_bus or DisplayEventBus.get_default()
        self._source = source

    def set_source(self, source: str) -> None:
        """设置事件来源标识（可在运行时切换）。"""
        self._source = source

    # ── 生命周期 ────────────────────────────────────────

    def start(self) -> None:
        self._bus.publish(SessionStarted(source=self._source))

    def stop(self, final: bool = False) -> None:
        self._bus.publish(SessionStopped(final=final, source=self._source))

    # ── 捕获显示函数输出 ────────────────────────────────

    def capture_and_print(self, display_func) -> str:
        """EventBusDisplayProxy 直接执行 display_func"""
        return display_func() if callable(display_func) else ""

    # ── 工具调用 ────────────────────────────────────────

    def tool_parsing(self, label: str, tool_name: str, arguments: str = "") -> None:
        self._bus.publish(ToolParsingEvent(
            label=label, tool_name=tool_name,
            arguments=arguments, source=self._source,
        ))

    def tool_start(
        self,
        label: str,
        tool_name: str,
        detail: str = "",
        metadata: dict | None = None,
    ) -> None:
        self._bus.publish(ToolStartedEvent(
            label=label, tool_name=tool_name,
            detail=detail, metadata=metadata,
            source=self._source,
        ))

    def tool_done(
        self,
        label: str,
        tool_name: str = "",
        success: bool = True,
        metadata: dict | None = None,
    ) -> None:
        self._bus.publish(ToolDoneEvent(
            label=label, tool_name=tool_name,
            success=success, metadata=metadata,
            source=self._source,
        ))

    # ── 状态 ────────────────────────────────────────────

    def update_status(self, label: str, status: str) -> None:
        self._bus.publish(AgentStatusChanged(
            label=label, status=status, source=self._source,
        ))

    def update_agent_status(self, label: str, status: str) -> None:
        """发布代理状态变更事件（DisplayPort 抽象方法实现）。"""
        self._bus.publish(AgentStatusChanged(
            label=label, status=status, source=self._source,
        ))

    def update_model_phase(self, label: str, phase: str, info: str = "") -> None:
        self._bus.publish(ModelPhaseEvent(
            label=label, phase=phase, info=info, source=self._source,
        ))

    def update_usage(
        self,
        label: str,
        usage: dict,
        replace: bool = False,
    ) -> None:
        self._bus.publish(UsageUpdatedEvent(
            label=label, usage=usage, replace=replace, source=self._source,
        ))

    # ── 额外公开方法（非 BaseDisplay 接口，但 ParallelDisplay 使用） ──

    def add_agent(self, label: str, description: str, status: str = "running") -> None:
        self._bus.publish(AgentAddedEvent(
            label=label, description=description, status=status, source=self._source,
        ))

    def tool_batch_start(self, label: str, tool_names: list) -> None:
        self._bus.publish(ToolBatchStartedEvent(
            label=label, tool_names=tuple(tool_names), source=self._source,
        ))

    def update_parse_info(self, label: str, tool_names: str, tokens: int, elapsed: float) -> None:
        self._bus.publish(ParseInfoEvent(
            label=label, tool_names=tool_names, tokens=tokens, elapsed=elapsed,
            source=self._source,
        ))

    def parse_info_done(self, label: str) -> None:
        self._bus.publish(ParseInfoDoneEvent(
            label=label, source=self._source,
        ))

    def update_tokens(self, label: str, tokens: int) -> None:
        self._bus.publish(TokenEvent(
            label=label, tokens=tokens, source=self._source,
        ))

    def update_live_output(self, label: str, tokens: int) -> None:
        self._bus.publish(LiveOutputEvent(
            label=label, tokens=tokens, source=self._source,
        ))

    def update_live_input(self, label: str, tokens: int) -> None:
        self._bus.publish(LiveInputEvent(
            label=label, tokens=tokens, source=self._source,
        ))

    def update_speed(self, label: str, speed: float) -> None:
        self._bus.publish(SpeedUpdatedEvent(
            label=label, speed=speed, source=self._source,
        ))

    def publish_output(self, text: str, level: str = "info") -> None:
        """发布通用输出事件（替代 print）。"""
        self._bus.publish(OutputEvent(
            text=text, level=level, source=self._source,
        ))

    def publish_tool_summary(
        self,
        successful_tools: List[str],
        failed_tools: List[Tuple[str, str]],
    ) -> None:
        """发布工具执行汇总事件。"""
        self._bus.publish(ToolSummaryEvent(
            successful_tools=tuple(successful_tools),
            failed_tools=tuple(failed_tools),
            source=self._source,
        ))


# ═══════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════

def wire_event_bus(
    display: Any,  # 鸭类型：DisplayEventAdapter 通过 hasattr/getattr 调用方法
    event_bus: Optional[DisplayEventBus] = None,
    source: str = "",
) -> EventBusDisplayProxy:
    """一键连接 EventBus 与显示层。

    创建 EventBusDisplayProxy 并订阅事件到 display。
    等价于同时使用模式B（代理发布）和模式A（代理消费）。

    Args:
        display: 任意对象，DisplayEventAdapter 通过 hasattr/getattr 调用其方法
                 （如 ToolExecutionDisplay / ParallelDisplay）
        event_bus: EventBus 实例。None 则使用默认单例。
        source: 事件来源标识

    Returns:
        EventBusDisplayProxy 实例（可作为 agent.display 使用）
    """
    bus = event_bus or DisplayEventBus.get_default()

    # 模式B：创建代理，将方法调用发布为事件
    proxy = EventBusDisplayProxy(bus, source=source)

    # 模式A：创建适配器，将事件消费到 display
    adapter = DisplayEventAdapter(display)
    adapter.subscribe_to(bus)

    return proxy
