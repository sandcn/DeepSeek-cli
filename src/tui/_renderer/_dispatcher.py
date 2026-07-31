"""事件分发模块 — EventDispatcher DisplayEvent→RenderCommand 过滤+入队。

从 ``_renderer.py`` 提取为独立子模块，ChatConfig 依赖替换为 filter_fn 注入。
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Callable, Union

from src.tui._const import (
    RenderCommand,
    RenderCmd,
    ReasoningCmd, ContentCmd, PhaseDoneCmd,
    ToolOutputCmd, ToolSummaryCmd,
    UserMsgCmd, ParseInfoCmd,
    NotificationCmd, WriteLineCmd,
    ToolCountIncCmd, ToolFailIncCmd, ErrorCmd, ToolCountDecCmd,
    SubagentFrameCmd, SplashCmd, MainPhaseCmd,
    _CLEAR_PARSE_LINE,
)
from src.tui._config import TuiConfig

if TYPE_CHECKING:
    from src.tui.events.event_types import (
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
        ToolParsingEvent,
        ToolStartedEvent,
        ToolSummaryEvent,
    )

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# EventDispatcher — 事件→命令映射
# ═══════════════════════════════════════════════════════════

class EventDispatcher:
    """DisplayEvent → RenderCommand 过滤+入队。

    将 12 种 DisplayEvent 类型映射到对应的 RenderCommand 并推入命令队列。
    使用注入的 ``filter_fn`` 替代直接持有 ChatConfig 进行 source/label 过滤。
    """

    def __init__(
        self,
        push_cmd: Callable[[Union[RenderCmd, tuple]], None],
        filter_fn: Callable[[str | None], bool] | None = None,
        *,
        main_label: str | None = None,
        max_error_length: int | None = None,
    ):
        """初始化 EventDispatcher。

        Args:
            push_cmd: 命令推送回调。
            filter_fn: source 过滤函数。
            main_label: 主 Agent label。
            max_error_length: 错误消息截断长度。
        """
        self._push_cmd = push_cmd
        self._filter_fn = filter_fn or self._default_filter_fn
        self._main_label = main_label or "default"
        self._max_error_length = max_error_length or TuiConfig.defaults().max_error_length
        self._custom_handlers: dict[type, Callable] = {}

    @staticmethod
    def _default_filter_fn(source: str | None) -> bool:
        """默认 source 过滤函数。"""
        if source is None:
            return False
        return source == "agent" or (source or "").startswith("agent-")

    def _is_agent_source(self, source: str | None) -> bool:
        if source is None:
            return False
        return self._filter_fn(source)

    def _is_main_label(self, label: str | None) -> bool:
        return label == self._main_label

    def register_handler(self, event_type: type, handler_method: Callable) -> None:
        self._custom_handlers[event_type] = handler_method

    def list_handlers(self) -> dict[type, Callable]:
        from src.tui.events import event_types as _ET
        result: dict[type, Callable] = {
            _ET.ReasoningChunkEvent: self._on_reasoning_chunk,
            _ET.ContentChunkEvent: self._on_content_chunk,
            _ET.PhaseDoneEvent: self._on_phase_done,
            _ET.ToolParsingEvent: self._on_tool_parsing,
            _ET.ToolStartedEvent: self._on_tool_started,
            _ET.ToolDoneEvent: self._on_tool_done,
            _ET.ToolOutputChunkEvent: self._on_tool_output,
            _ET.ParseInfoEvent: self._on_parse_info,
            _ET.ParseInfoDoneEvent: self._on_parse_info_done,
            _ET.OutputEvent: self._on_output,
            _ET.ModelPhaseEvent: self._on_model_phase,
            _ET.ToolSummaryEvent: self._on_tool_summary,
        }
        result.update(self._custom_handlers)
        return result

    # ── 事件处理器 ────────────────────────────────

    def _on_reasoning_chunk(self, event: "ReasoningChunkEvent") -> None:
        if event.label != self._main_label:
            return
        if not event.text:
            return
        self._push_cmd(ReasoningCmd(text=event.text))

    def _on_content_chunk(self, event: "ContentChunkEvent") -> None:
        if event.label != self._main_label:
            return
        if not event.text:
            return
        self._push_cmd(ContentCmd(text=event.text))

    def _on_tool_parsing(self, event: "ToolParsingEvent") -> None:
        if not self._is_agent_source(event.source):
            return
        self._push_cmd(MainPhaseCmd(phase="parsing"))

    def _on_phase_done(self, event: "PhaseDoneEvent") -> None:
        if event.label != self._main_label:
            return
        self._push_cmd(PhaseDoneCmd(phase=event.phase))

    @staticmethod
    def _is_subagent_label(label: str) -> bool:
        return bool(label and label.startswith("agent-"))

    def _on_tool_started(self, event: "ToolStartedEvent") -> None:
        if not self._is_agent_source(event.source) and not self._is_subagent_label(event.label):
            return
        self._push_cmd(ToolCountIncCmd())

    def _on_tool_done(self, event: "ToolDoneEvent") -> None:
        if not self._is_agent_source(event.source) and not self._is_subagent_label(event.label):
            return
        if not event.success:
            self._push_cmd(ToolFailIncCmd())
            self._push_cmd(ToolCountDecCmd())
        else:
            self._push_cmd(ToolCountDecCmd())

    def _on_tool_output(self, event: "ToolOutputChunkEvent") -> None:
        if not self._is_agent_source(event.source):
            return
        text = event.text.rstrip("\n")
        if text:
            self._push_cmd(ToolOutputCmd(text=text))

    def _on_parse_info(self, event: "ParseInfoEvent") -> None:
        if not self._is_agent_source(event.source):
            return
        self._push_cmd(ParseInfoCmd(tool_names=event.tool_names, tokens=event.tokens, elapsed=event.elapsed))

    def _on_parse_info_done(self, event: "ParseInfoDoneEvent") -> None:
        if not self._is_agent_source(event.source):
            return
        self._push_cmd(ParseInfoCmd(tool_names="", tokens=_CLEAR_PARSE_LINE, elapsed=0.0))

    def _on_output(self, event: "OutputEvent") -> None:
        if not event.text:
            return
        self._push_cmd(WriteLineCmd(text=event.text))

    def _on_model_phase(self, event: "ModelPhaseEvent") -> None:
        if event.label != self._main_label:
            return
        if event.phase != "error":
            self._push_cmd(MainPhaseCmd(phase=event.phase))
            return
        if not event.info:
            return
        _info = event.info
        if len(_info) > self._max_error_length:
            _info = _info[:self._max_error_length] + "..."
        self._push_cmd(ErrorCmd(message=_info))

    def _on_tool_summary(self, event: "ToolSummaryEvent") -> None:
        if not self._is_agent_source(event.source):
            return
        if not event.successful_tools and not event.failed_tools:
            return
        self._push_cmd(ToolSummaryCmd(successful=event.successful_tools, failed=event.failed_tools))


__all__ = ["EventDispatcher"]
