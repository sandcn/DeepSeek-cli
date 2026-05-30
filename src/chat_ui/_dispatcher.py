"""EventDispatcher — EventBus 事件订阅/过滤/入队。

11 种事件处理器统一管理，在事件线程中仅做过滤+入队（非阻塞），
渲染由 Reader 线程串行消费。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from ..ui.events.event_types import (
    ContentChunkEvent,
    DisplayEvent,
    ModelPhaseEvent,
    OutputEvent,
    ParseInfoDoneEvent,
    ParseInfoEvent,
    PhaseDoneEvent,
    ReasoningChunkEvent,
    ToolDoneEvent,
    ToolOutputChunkEvent,
    ToolStartedEvent,
    ToolSummaryEvent,
)
from ._const import (
    _CLEAR_PARSE_LINE,
    _MAIN_LABEL,
    _MAIN_SOURCE,
    RenderCommand,
)

if TYPE_CHECKING:
    from ..ui.events.event_bus import DisplayEventBus


class EventDispatcher:
    """EventBus 事件订阅/过滤/入队。

    持有事件类型→处理器映射表，提供 subscribe/unsubscribe 方法。
    所有处理器在 EventBus 回调线程中执行：仅过滤+入队，不直接 I/O。
    """

    # ── 事件处理器注册表（subscribe/unsubscribe 复用） ──
    _EVENT_HANDLERS: tuple[tuple[type, str], ...] = (
        (ReasoningChunkEvent,    "_on_reasoning_chunk"),
        (ContentChunkEvent,      "_on_content_chunk"),
        (PhaseDoneEvent,         "_on_phase_done"),
        (ToolStartedEvent,       "_on_tool_started"),
        (ToolDoneEvent,          "_on_tool_done"),
        (ToolOutputChunkEvent,   "_on_tool_output"),
        (ToolSummaryEvent,       "_on_tool_summary"),
        (ParseInfoEvent,         "_on_parse_info"),
        (ParseInfoDoneEvent,     "_on_parse_info_done"),
        (ModelPhaseEvent,        "_on_model_phase"),
        (OutputEvent,            "_on_output"),
    )

    def __init__(self, bus: DisplayEventBus, push_cmd: Callable):
        """初始化 EventDispatcher。

        Args:
            bus: DisplayEventBus 实例
            push_cmd: 入队回调函数（由 ChatUIConsumer._push_cmd 提供）
        """
        self._bus = bus
        self._push_cmd = push_cmd

        # 预绑定事件处理器（确保 subscribe/unsubscribe 使用同一 bound method 对象）
        self._bound_handlers: dict[type, Callable] = {
            event_type: getattr(self, handler_name)
            for event_type, handler_name in self._EVENT_HANDLERS
        }

    def subscribe(self) -> None:
        """订阅所有事件。幂等。"""
        for event_type in self._bound_handlers:
            self._bus.subscribe(self._bound_handlers[event_type], event_type=event_type)

    def unsubscribe(self) -> None:
        """取消订阅所有事件。幂等。"""
        for event_type in self._bound_handlers:
            self._bus.unsubscribe(self._bound_handlers[event_type], event_type=event_type)

    # ── 事件来源过滤 ─────────────────────────────────

    @staticmethod
    def _is_agent_source(source: str) -> bool:
        """判断事件来源是否与 Agent/SubAgent 相关。

        ChatUI 需要同时显示主 Agent 和 SubAgent 的工具调用状态：
        - 主 Agent 使用 source="agent"（_MAIN_SOURCE）
        - SubAgent 使用 source=self.label（例如 "agent-1", "agent-2"）

        返回 True 表示该来源应被 ChatUI 消费（工具计数/输出显示）。
        """
        return source == _MAIN_SOURCE or source.startswith("agent-")

    # ── 事件处理器 ───────────────────────────────────

    def _on_reasoning_chunk(self, event: DisplayEvent) -> None:
        if not isinstance(event, ReasoningChunkEvent):
            return
        if event.label != _MAIN_LABEL or not event.text:
            return
        self._push_cmd((RenderCommand.REASONING, event.text))

    def _on_content_chunk(self, event: DisplayEvent) -> None:
        if not isinstance(event, ContentChunkEvent):
            return
        if event.label != _MAIN_LABEL or not event.text:
            return
        self._push_cmd((RenderCommand.CONTENT, event.text))

    def _on_phase_done(self, event: DisplayEvent) -> None:
        if not isinstance(event, PhaseDoneEvent):
            return
        if event.label != _MAIN_LABEL:
            return
        self._push_cmd((RenderCommand.PHASE_DONE, event.phase))

    def _on_tool_started(self, event: DisplayEvent) -> None:
        if not isinstance(event, ToolStartedEvent):
            return
        if not self._is_agent_source(event.source):
            return
        self._push_cmd((RenderCommand.TOOL_COUNT_INC,))

    def _on_tool_done(self, event: DisplayEvent) -> None:
        if not isinstance(event, ToolDoneEvent):
            return
        if not self._is_agent_source(event.source):
            return
        if not event.success:
            self._push_cmd((RenderCommand.TOOL_FAIL_INC,))

    def _on_tool_output(self, event: DisplayEvent) -> None:
        if not isinstance(event, ToolOutputChunkEvent):
            return
        if not self._is_agent_source(event.source):
            return
        text = event.text.rstrip("\n")
        if text:
            self._push_cmd((RenderCommand.TOOL_OUTPUT, text))

    def _on_parse_info(self, event: DisplayEvent) -> None:
        if not isinstance(event, ParseInfoEvent):
            return
        if not self._is_agent_source(event.source):
            return
        self._push_cmd((RenderCommand.PARSE_INFO, event.tool_names, event.tokens, event.elapsed))

    def _on_parse_info_done(self, event: DisplayEvent) -> None:
        if not isinstance(event, ParseInfoDoneEvent):
            return
        if not self._is_agent_source(event.source):
            return
        self._push_cmd((RenderCommand.PARSE_INFO, "", _CLEAR_PARSE_LINE, 0.0))

    def _on_output(self, event: DisplayEvent) -> None:
        if not isinstance(event, OutputEvent):
            return
        if not event.text:
            return
        if event.source == "cmd":
            self._push_cmd((RenderCommand.CMD_OUTPUT, event.text))
        else:
            self._push_cmd((RenderCommand.WRITE_LINE, event.text))

    def _on_model_phase(self, event: DisplayEvent) -> None:
        """处理模型阶段变更事件，phase="error" 时渲染错误到上屏。"""
        if not isinstance(event, ModelPhaseEvent):
            return
        if event.label != _MAIN_LABEL:
            return
        if event.phase != "error":
            return
        if not event.info:
            return

        _MAX_ERROR_LENGTH = 200
        info = (
            event.info[:_MAX_ERROR_LENGTH] + "..."
            if len(event.info) > _MAX_ERROR_LENGTH
            else event.info
        )
        self._push_cmd((RenderCommand.ERROR, info))

    def _on_tool_summary(self, event: DisplayEvent) -> None:
        if not isinstance(event, ToolSummaryEvent):
            return
        if not self._is_agent_source(event.source):
            return
        if not event.successful_tools and not event.failed_tools:
            return
        self._push_cmd((RenderCommand.TOOL_SUMMARY, event.successful_tools, event.failed_tools))
