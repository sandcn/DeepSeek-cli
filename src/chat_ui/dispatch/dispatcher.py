"""事件分发器 — DisplayEvent → 渲染命令 dataclass 过滤+入队。

从 _tui.py 拆分，11 种事件类型映射到对应命令 dataclass。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from ..commands.const import (
    _MAIN_LABEL, _MAIN_SOURCE,
    _CLEAR_PARSE_LINE,
    _MAX_ERROR_LENGTH,
)

from ..commands.types import (
    CmdReasoning,
    CmdContent,
    CmdPhaseDone,
    CmdToolOutput,
    CmdToolSummary,
    CmdUserMsg,
    CmdParseInfo,
    CmdNotification,
    CmdWriteLine,
    CmdDisplayMsgs,
    CmdToolCountInc,
    CmdToolFailInc,
    CmdToolCountDec,
    CmdError,
)

from ..infrastructure.utils import _truncate_msg

if TYPE_CHECKING:
    from ...ui.events.event_types import (
        ReasoningChunkEvent,
        ContentChunkEvent,
        PhaseDoneEvent,
        ToolDoneEvent,
        ToolOutputChunkEvent,
        ToolStartedEvent,
        ToolSummaryEvent,
        ParseInfoEvent,
        ParseInfoDoneEvent,
        OutputEvent,
        ModelPhaseEvent,
    )


# ═══════════════════════════════════════════════════════════
# 事件处理映射表
# ═══════════════════════════════════════════════════════════

_HANDLER_MAP: dict[str, tuple[str, str]] = {
    "ReasoningChunkEvent":  ("ReasoningChunkEvent",  "_on_reasoning_chunk"),
    "ContentChunkEvent":    ("ContentChunkEvent",    "_on_content_chunk"),
    "PhaseDoneEvent":       ("PhaseDoneEvent",       "_on_phase_done"),
    "ToolStartedEvent":     ("ToolStartedEvent",     "_on_tool_started"),
    "ToolDoneEvent":        ("ToolDoneEvent",        "_on_tool_done"),
    "ToolOutputChunkEvent": ("ToolOutputChunkEvent", "_on_tool_output"),
    "ParseInfoEvent":       ("ParseInfoEvent",       "_on_parse_info"),
    "ParseInfoDoneEvent":   ("ParseInfoDoneEvent",   "_on_parse_info_done"),
    "OutputEvent":          ("OutputEvent",          "_on_output"),
    "ModelPhaseEvent":      ("ModelPhaseEvent",      "_on_model_phase"),
    "ToolSummaryEvent":     ("ToolSummaryEvent",     "_on_tool_summary"),
}


# ═══════════════════════════════════════════════════════════
# EventDispatcher
# ═══════════════════════════════════════════════════════════

class EventDispatcher:
    """DisplayEvent → RenderCommand 过滤+入队。

    将 11 种 DisplayEvent 类型映射到对应的 RenderCommand 并推入命令队列：

    - ReasoningChunkEvent  → REASONING       (推理内容块)
    - ContentChunkEvent    → CONTENT         (助手回答块)
    - PhaseDoneEvent       → PHASE_DONE      (推理/内容阶段完成)
    - ToolStartedEvent     → TOOL_COUNT_INC  (工具开始计数+1)
    - ToolDoneEvent        → TOOL_COUNT_DEC / TOOL_FAIL_INC (工具完成/失败)
    - ToolOutputChunkEvent → TOOL_OUTPUT     (工具输出内容)
    - ToolSummaryEvent     → TOOL_SUMMARY    (工具汇总块)
    - ParseInfoEvent       → PARSE_INFO      (解析进度信息)
    - ParseInfoDoneEvent   → PARSE_INFO      (解析完成清行)
    - OutputEvent          → WRITE_LINE      (样式化行输出)
    - ModelPhaseEvent      → ERROR           (模型错误阶段)

    所有事件经过 label/source 过滤后才入队，非主 Agent 事件被丢弃。
    """

    def __init__(self, push_cmd: Callable[[object], None]):
        self._push_cmd = push_cmd

    @staticmethod
    def _is_agent_source(source: str | None) -> bool:
        if source is None:
            return False
        return source == _MAIN_SOURCE or source.startswith("agent-")

    def _on_reasoning_chunk(self, event) -> None:
        from ...ui.events.event_types import ReasoningChunkEvent  # 运行时 isinstance 需要
        if not isinstance(event, ReasoningChunkEvent):
            return
        if event.label != _MAIN_LABEL or not event.text:
            return
        self._push_cmd(CmdReasoning(text=event.text))

    def _on_content_chunk(self, event) -> None:
        from ...ui.events.event_types import ContentChunkEvent  # 运行时 isinstance 需要
        if not isinstance(event, ContentChunkEvent):
            return
        if event.label != _MAIN_LABEL or not event.text:
            return
        self._push_cmd(CmdContent(text=event.text))

    def _on_phase_done(self, event) -> None:
        from ...ui.events.event_types import PhaseDoneEvent  # 运行时 isinstance 需要
        if not isinstance(event, PhaseDoneEvent):
            return
        if event.label != _MAIN_LABEL:
            return
        self._push_cmd(CmdPhaseDone(phase=event.phase))

    def _on_tool_started(self, event) -> None:
        from ...ui.events.event_types import ToolStartedEvent  # 运行时 isinstance 需要
        if not isinstance(event, ToolStartedEvent):
            return
        if not self._is_agent_source(event.source):
            return
        self._push_cmd(CmdToolCountInc())

    def _on_tool_done(self, event) -> None:
        from ...ui.events.event_types import ToolDoneEvent  # 运行时 isinstance 需要
        if not isinstance(event, ToolDoneEvent):
            return
        if not self._is_agent_source(event.source):
            return
        if not event.success:
            self._push_cmd(CmdToolFailInc())
            self._push_cmd(CmdToolCountDec())
        else:
            self._push_cmd(CmdToolCountDec())

    def _on_tool_output(self, event) -> None:
        from ...ui.events.event_types import ToolOutputChunkEvent  # 运行时 isinstance 需要
        if not isinstance(event, ToolOutputChunkEvent):
            return
        if not self._is_agent_source(event.source):
            return
        text = event.text.rstrip("\n")
        if text:
            self._push_cmd(CmdToolOutput(text=text))

    def _on_parse_info(self, event) -> None:
        from ...ui.events.event_types import ParseInfoEvent  # 运行时 isinstance 需要
        if not isinstance(event, ParseInfoEvent):
            return
        if not self._is_agent_source(event.source):
            return
        self._push_cmd(CmdParseInfo(
            tool_names=event.tool_names, tokens=event.tokens, elapsed=event.elapsed,
        ))

    def _on_parse_info_done(self, event) -> None:
        from ...ui.events.event_types import ParseInfoDoneEvent  # 运行时 isinstance 需要
        if not isinstance(event, ParseInfoDoneEvent):
            return
        if not self._is_agent_source(event.source):
            return
        self._push_cmd(CmdParseInfo(tool_names="", tokens=_CLEAR_PARSE_LINE, elapsed=0.0))

    def _on_output(self, event) -> None:
        from ...ui.events.event_types import OutputEvent  # 运行时 isinstance 需要
        if not isinstance(event, OutputEvent):
            return
        if not event.text:
            return
        self._push_cmd(CmdWriteLine(text=event.text))

    def _on_model_phase(self, event) -> None:
        from ...ui.events.event_types import ModelPhaseEvent  # 运行时 isinstance 需要
        if not isinstance(event, ModelPhaseEvent):
            return
        if event.label != _MAIN_LABEL:
            return
        if event.phase != "error":
            return
        if not event.info:
            return
        info = _truncate_msg(event.info, _MAX_ERROR_LENGTH)
        self._push_cmd(CmdError(message=info))

    def _on_tool_summary(self, event) -> None:
        from ...ui.events.event_types import ToolSummaryEvent  # 运行时 isinstance 需要
        if not isinstance(event, ToolSummaryEvent):
            return
        if not self._is_agent_source(event.source):
            return
        if not event.successful_tools and not event.failed_tools:
            return
        self._push_cmd(CmdToolSummary(
            successful=event.successful_tools, failed=event.failed_tools,
        ))
