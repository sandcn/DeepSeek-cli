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

# ⚠ 本文件保留独立实现，不可替换为 tui_framework.events
# 原因: 导出 25 种应用特化事件类型（ToolStartedEvent 等），
# framework 的事件系统为通用框架事件（KeyPressEvent 等）。

from .event_types import (
    DisplayEvent,
    SessionStarted, SessionStopped,
    ToolParsingEvent, ToolStartedEvent, ToolDoneEvent, ToolOutputChunkEvent, ToolBatchStartedEvent,
    AgentAddedEvent, AgentStatusChanged,
    ModelPhaseEvent, PhaseDoneEvent, UsageUpdatedEvent,
    ContentChunkEvent, ReasoningChunkEvent,
    ParseInfoEvent, ParseInfoDoneEvent, TokenEvent, LiveOutputEvent, LiveInputEvent, SpeedUpdatedEvent,
    OutputEvent, ToolSummaryEvent,
    UserSelectNeededEvent, AgentResultEvent,
    ALL_EVENT_TYPES,
)
from .event_bus import DisplayEventBus, EventHandler
from .event_pool import EventPool
from .consumers import (
    OutputConsumer,
    publish_output,
    publish_tool_summary,
)

__all__ = [
    # 事件类型
    "DisplayEvent",
    "SessionStarted", "SessionStopped",
    "ToolParsingEvent", "ToolStartedEvent", "ToolDoneEvent", "ToolOutputChunkEvent", "ToolBatchStartedEvent",
    "AgentAddedEvent", "AgentStatusChanged", "AgentResultEvent",
    "ModelPhaseEvent", "PhaseDoneEvent", "UsageUpdatedEvent",
    "ContentChunkEvent", "ReasoningChunkEvent",
    "ParseInfoEvent", "ParseInfoDoneEvent", "TokenEvent", "LiveOutputEvent", "LiveInputEvent", "SpeedUpdatedEvent",
    "OutputEvent", "ToolSummaryEvent", "UserSelectNeededEvent",
    "ALL_EVENT_TYPES",
    # 基础设施
    "DisplayEventBus",
    "EventHandler",
    "EventPool",
    # 消费者
    "OutputConsumer",
    # 便捷函数
    "publish_output",
    "publish_tool_summary",
]
