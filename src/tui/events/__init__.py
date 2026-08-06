"""UI 显示层事件驱动架构

提供类型化事件、事件总线和适配器，解耦显示生产者与消费者。

快速开始：
    from tui.events import DisplayEventBus, ToolStartedEvent

    # 发布事件
    DisplayEventBus.get_default().publish(
        ToolStartedEvent(label="call_xxx", tool_name="read_file", source="agent")
    )

    # 订阅事件
    DisplayEventBus.get_default().subscribe(my_handler)
"""

from .event_types import (
    DisplayEvent,
    SessionStarted, SessionStopped,
    ToolParsingEvent, ToolStartedEvent, ToolDoneEvent, ToolOutputChunkEvent, ToolBatchStartedEvent,
    AgentAddedEvent, AgentStatusChanged,
    ModelPhaseEvent, PhaseDoneEvent, UsageUpdatedEvent,
    ContentChunkEvent, ReasoningChunkEvent,
    ParseInfoEvent, ParseInfoDoneEvent, MetricsUpdateEvent,
    OutputEvent, ToolSummaryEvent,
    UserSelectNeededEvent, SubagentPromptEvent, AgentResultEvent,
    BackgroundTaskChangedEvent,
    ALL_EVENT_TYPES,
)
from .event_bus import DisplayEventBus, EventHandler
from .consumers import (
    OutputConsumer,
    publish_output,
    publish_tool_summary,
)
from .adapters import (
    DisplayEventAdapter,
    EventBusDisplayProxy,
    wire_event_bus,
)

__all__ = [
    # 事件类型
    "DisplayEvent",
    "SessionStarted", "SessionStopped",
    "ToolParsingEvent", "ToolStartedEvent", "ToolDoneEvent", "ToolOutputChunkEvent", "ToolBatchStartedEvent",
    "AgentAddedEvent", "AgentStatusChanged", "AgentResultEvent",
    "SubagentPromptEvent",
    "ModelPhaseEvent", "PhaseDoneEvent", "UsageUpdatedEvent",
    "ContentChunkEvent", "ReasoningChunkEvent",
    "ParseInfoEvent", "ParseInfoDoneEvent", "MetricsUpdateEvent",
    "OutputEvent", "ToolSummaryEvent", "UserSelectNeededEvent",
    "BackgroundTaskChangedEvent",
    "ALL_EVENT_TYPES",
    # 基础设施
    "DisplayEventBus",
    "EventHandler",
    # 消费者
    "OutputConsumer",
    # 适配器
    "DisplayEventAdapter",
    "EventBusDisplayProxy",
    "wire_event_bus",
    # 便捷函数
    "publish_output",
    "publish_tool_summary",
]
