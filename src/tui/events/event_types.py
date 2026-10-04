"""UI 显示层事件类型 — 兼容 re-export 层

显示事件类型定义已下沉至核心层 ``core.events.display_types``（架构改进：
解除 core → tui 反向依赖）。本模块保持旧导入路径
``src.tui.events.event_types`` 兼容（表现层与测试引用），全部符号从核心层
re-export。新增事件类型请定义在 ``src/core/events/display_types.py``。
"""

from __future__ import annotations

from ...core.events.display_types import *  # noqa: F401,F403
from ...core.events.display_types import ALL_EVENT_TYPES

__all__ = [
    "DisplayEvent",
    "SessionStarted", "SessionStopped",
    "ToolParsingEvent", "ToolStartedEvent", "ToolDoneEvent",
    "ToolOutputChunkEvent", "ToolBatchStartedEvent", "ToolNoticeEvent",
    "AgentAddedEvent", "AgentStatusChanged",
    "ModelPhaseEvent", "PhaseDoneEvent", "UsageUpdatedEvent",
    "ContentChunkEvent", "ReasoningChunkEvent",
    "ParseInfoEvent", "ParseInfoDoneEvent", "MetricsUpdateEvent",
    "OutputEvent", "ToolSummaryEvent",
    "SubagentPromptEvent", "AgentResultEvent",
    "BackgroundTaskChangedEvent",
    "ALL_EVENT_TYPES",
]
