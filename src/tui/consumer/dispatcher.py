"""事件分发器 — DisplayEvent → RenderCommand 过滤+入队。

从 _tui.py 拆分，11 种事件类型映射到对应 RenderCommand。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from .const import (
    RenderCommand,
    _MAIN_LABEL, _MAIN_SOURCE,
    _CLEAR_PARSE_LINE,
    _MAX_ERROR_LENGTH,
)

from .utils import _truncate_msg

if TYPE_CHECKING:
    from ..events.event_types import (
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

from ..events import event_types as _EVENT_TYPES


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
    使用 _pre_filter() 统一前置过滤消除重复过滤判断。
    """

    def __init__(self, push_cmd: Callable[[tuple], None]):
        self._push_cmd = push_cmd

    @staticmethod
    def _is_agent_source(source: str | None) -> bool:
        if source is None:
            return False
        return source == _MAIN_SOURCE or source.startswith("agent-")

    @staticmethod
    def _pre_filter(event, event_type, *, require_label=False, require_source=False) -> bool:
        """统一前置过滤：不满足条件返回 False（应跳过该事件）。

        取代各 handler 中重复的 isinstance/label/source 判断。
        """
        if not isinstance(event, event_type):
            return False
        if require_label and event.label != _MAIN_LABEL:
            return False
        if require_source and not EventDispatcher._is_agent_source(event.source):
            return False
        return True

    def _on_reasoning_chunk(self, event) -> None:
        if not self._pre_filter(event, _EVENT_TYPES.ReasoningChunkEvent, require_label=True):
            return
        if not event.text:
            return
        self._push_cmd((RenderCommand.REASONING, event.text))

    def _on_content_chunk(self, event) -> None:
        if not self._pre_filter(event, _EVENT_TYPES.ContentChunkEvent, require_label=True):
            return
        if not event.text:
            return
        self._push_cmd((RenderCommand.CONTENT, event.text))

    def _on_phase_done(self, event) -> None:
        if not self._pre_filter(event, _EVENT_TYPES.PhaseDoneEvent, require_label=True):
            return
        self._push_cmd((RenderCommand.PHASE_DONE, event.phase))

    def _on_tool_started(self, event) -> None:
        if not self._pre_filter(event, _EVENT_TYPES.ToolStartedEvent, require_source=True):
            return
        self._push_cmd((RenderCommand.TOOL_COUNT_INC,))

    def _on_tool_done(self, event) -> None:
        if not self._pre_filter(event, _EVENT_TYPES.ToolDoneEvent, require_source=True):
            return
        if not event.success:
            self._push_cmd((RenderCommand.TOOL_FAIL_INC,))
            self._push_cmd((RenderCommand.TOOL_COUNT_DEC,))
        else:
            self._push_cmd((RenderCommand.TOOL_COUNT_DEC,))

    def _on_tool_output(self, event) -> None:
        if not self._pre_filter(event, _EVENT_TYPES.ToolOutputChunkEvent, require_source=True):
            return
        text = event.text.rstrip("\n")
        if text:
            self._push_cmd((RenderCommand.TOOL_OUTPUT, text))

    def _on_parse_info(self, event) -> None:
        if not self._pre_filter(event, _EVENT_TYPES.ParseInfoEvent, require_source=True):
            return
        self._push_cmd((RenderCommand.PARSE_INFO, event.tool_names, event.tokens, event.elapsed))

    def _on_parse_info_done(self, event) -> None:
        if not self._pre_filter(event, _EVENT_TYPES.ParseInfoDoneEvent, require_source=True):
            return
        self._push_cmd((RenderCommand.PARSE_INFO, "", _CLEAR_PARSE_LINE, 0.0))

    def _on_output(self, event) -> None:
        if not self._pre_filter(event, _EVENT_TYPES.OutputEvent):
            return
        if not event.text:
            return
        self._push_cmd((RenderCommand.WRITE_LINE, event.text))

    def _on_model_phase(self, event) -> None:
        if not self._pre_filter(event, _EVENT_TYPES.ModelPhaseEvent, require_label=True):
            return
        if event.phase != "error":
            return
        if not event.info:
            return
        info = _truncate_msg(event.info, _MAX_ERROR_LENGTH)
        self._push_cmd((RenderCommand.ERROR, info))

    def _on_tool_summary(self, event) -> None:
        if not self._pre_filter(event, _EVENT_TYPES.ToolSummaryEvent, require_source=True):
            return
        if not event.successful_tools and not event.failed_tools:
            return
        self._push_cmd((RenderCommand.TOOL_SUMMARY, event.successful_tools, event.failed_tools))
