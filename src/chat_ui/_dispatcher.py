"""chat_ui 事件分发模块 — EventBus 事件过滤+入队。

Layer 2 — 依赖 _const（RenderCommand + _MAIN_SOURCE + _MAIN_LABEL）。
通过 _push_cmd 回调将渲染命令写入队列，与 _consumer 解耦。

2026-06-11 简化：移除 @event_handler 装饰器 + importlib 反射机制，
改用直接 import 事件类型 + _HANDLER_MAP 字典映射。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from ..ui.events.event_types import (
    ReasoningChunkEvent, ContentChunkEvent, PhaseDoneEvent,
    ToolStartedEvent, ToolDoneEvent, ToolOutputChunkEvent,
    ToolSummaryEvent, ParseInfoEvent, ParseInfoDoneEvent,
    OutputEvent, ModelPhaseEvent,
)
from ._const import (
    _CLEAR_PARSE_LINE, _MAIN_LABEL, _MAIN_SOURCE, _MAX_ERROR_LENGTH,
    RenderCommand,
)
from ._utils import _truncate_msg

if TYPE_CHECKING:
    from ..ui.events.event_types import DisplayEvent


# ── 事件处理器映射表（handler_name → (event_type, method_name)） ──
# 直接映射，消除 importlib 反射 + 装饰器注册表。
_HANDLER_MAP: dict[str, tuple[type, str]] = {
    "ReasoningChunkEvent":  (ReasoningChunkEvent,  "_on_reasoning_chunk"),
    "ContentChunkEvent":    (ContentChunkEvent,    "_on_content_chunk"),
    "PhaseDoneEvent":       (PhaseDoneEvent,       "_on_phase_done"),
    "ToolStartedEvent":     (ToolStartedEvent,     "_on_tool_started"),
    "ToolDoneEvent":        (ToolDoneEvent,        "_on_tool_done"),
    "ToolOutputChunkEvent": (ToolOutputChunkEvent, "_on_tool_output"),
    "ParseInfoEvent":       (ParseInfoEvent,       "_on_parse_info"),
    "ParseInfoDoneEvent":   (ParseInfoDoneEvent,   "_on_parse_info_done"),
    "OutputEvent":          (OutputEvent,          "_on_output"),
    "ModelPhaseEvent":      (ModelPhaseEvent,      "_on_model_phase"),
    "ToolSummaryEvent":     (ToolSummaryEvent,     "_on_tool_summary"),
}


class EventDispatcher:
    """事件过滤+入队处理器。

    将 11 种 DisplayEvent 子类型过滤并转换为 RenderCommand 元组入队。
    与 ChatUIConsumer 解耦——仅依赖 push_cmd 回调，不感知队列实现细节。

    设计要点：
      - 每个 handler 先 isinstance 类型守卫，再过滤 label/source
      - handler 在 EventBus 回调线程中执行（非阻塞，仅过滤+入队）
      - SubAgent 事件通过 _is_agent_source 识别（source 前缀 "agent-"）
    """

    def __init__(self, push_cmd: Callable[[tuple], None]):
        self._push_cmd = push_cmd

    @staticmethod
    def _is_agent_source(source: str | None) -> bool:
        """判断事件来源是否与 Agent/SubAgent 相关。

        ChatUI 需要同时显示主 Agent 和 SubAgent 的工具调用状态：
        - 主 Agent 使用 source="agent"（_MAIN_SOURCE）
        - SubAgent 使用 source=self.label（例如 "agent-1", "agent-2"）

        返回 True 表示该来源应被 ChatUI 消费（工具计数/输出显示）。
        None 来源（事件构造异常/缺失字段）安全返回 False。
        """
        if source is None:
            return False
        return source == _MAIN_SOURCE or source.startswith("agent-")

    def _on_reasoning_chunk(self, event: "DisplayEvent") -> None:
        if not isinstance(event, ReasoningChunkEvent):
            return
        if event.label != _MAIN_LABEL or not event.text:
            return
        self._push_cmd((RenderCommand.REASONING, event.text))

    def _on_content_chunk(self, event: "DisplayEvent") -> None:
        if not isinstance(event, ContentChunkEvent):
            return
        if event.label != _MAIN_LABEL or not event.text:
            return
        self._push_cmd((RenderCommand.CONTENT, event.text))

    def _on_phase_done(self, event: "DisplayEvent") -> None:
        if not isinstance(event, PhaseDoneEvent):
            return
        if event.label != _MAIN_LABEL:
            return
        self._push_cmd((RenderCommand.PHASE_DONE, event.phase))

    def _on_tool_started(self, event: "DisplayEvent") -> None:
        if not isinstance(event, ToolStartedEvent):
            return
        if not self._is_agent_source(event.source):
            return
        self._push_cmd((RenderCommand.TOOL_COUNT_INC,))
        # 推送工具输出区域开始命令，携带工具名称和参数摘要
        self._push_cmd((RenderCommand.TOOL_OUTPUT_START, event.tool_name, event.detail))

    def _on_tool_done(self, event: "DisplayEvent") -> None:
        if not isinstance(event, ToolDoneEvent):
            return
        if not self._is_agent_source(event.source):
            return
        if not event.success:
            self._push_cmd((RenderCommand.TOOL_FAIL_INC,))
            self._push_cmd((RenderCommand.TOOL_COUNT_DEC,))
        else:
            self._push_cmd((RenderCommand.TOOL_COUNT_DEC,))
        # 推送工具输出区域结束命令
        self._push_cmd((RenderCommand.TOOL_OUTPUT_END, event.tool_name, event.success))

    def _on_tool_output(self, event: "DisplayEvent") -> None:
        if not isinstance(event, ToolOutputChunkEvent):
            return
        if not self._is_agent_source(event.source):
            return
        text = event.text.rstrip("\n")
        if text:
            self._push_cmd((RenderCommand.TOOL_OUTPUT, text))

    def _on_parse_info(self, event: "DisplayEvent") -> None:
        if not isinstance(event, ParseInfoEvent):
            return
        if not self._is_agent_source(event.source):
            return
        self._push_cmd((RenderCommand.PARSE_INFO, event.tool_names, event.tokens, event.elapsed))

    def _on_parse_info_done(self, event: "DisplayEvent") -> None:
        if not isinstance(event, ParseInfoDoneEvent):
            return
        if not self._is_agent_source(event.source):
            return
        self._push_cmd((RenderCommand.PARSE_INFO, "", _CLEAR_PARSE_LINE, 0.0))

    def _on_output(self, event: "DisplayEvent") -> None:
        if not isinstance(event, OutputEvent):
            return
        if not event.text:
            return
        # ★ 所有 OutputEvent 统一走 WRITE_LINE（CMD_OUTPUT 已废弃）
        self._push_cmd((RenderCommand.WRITE_LINE, event.text))

    def _on_model_phase(self, event: "DisplayEvent") -> None:
        """处理模型阶段变更事件，phase="error" 时渲染错误到上屏。

        拦截 ModelPhaseEvent 中的 phase="error" 事件，
        将错误消息通过 RenderCommand.ERROR 管道渲染为红色 [警告] 样式。

        过滤条件（四条件 AND）：
        1. isinstance 类型守卫
        2. label == _MAIN_LABEL（仅主 Agent，SubAgent 跳过）
        3. phase == "error"（非 error phase 跳过）
        4. info 非空（空消息跳过）
        """
        if not isinstance(event, ModelPhaseEvent):
            return
        if event.label != _MAIN_LABEL:
            return
        if event.phase != "error":
            return
        if not event.info:
            return

        # 截断超长 info 防止终端溢出
        info = _truncate_msg(event.info, _MAX_ERROR_LENGTH)
        self._push_cmd((RenderCommand.ERROR, info))

    def _on_tool_summary(self, event: "DisplayEvent") -> None:
        if not isinstance(event, ToolSummaryEvent):
            return
        if not self._is_agent_source(event.source):
            return
        if not event.successful_tools and not event.failed_tools:
            return
        self._push_cmd((RenderCommand.TOOL_SUMMARY, event.successful_tools, event.failed_tools))
