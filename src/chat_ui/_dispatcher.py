"""chat_ui 事件分发模块 — EventBus 事件过滤+入队。

Layer 2 — 依赖 _const（RenderCommand + _MAIN_SOURCE + _MAIN_LABEL）。
通过 _push_cmd 回调将渲染命令写入队列，与 _consumer 解耦。
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Callable

from ._const import (
    _CLEAR_PARSE_LINE, _MAIN_LABEL, _MAIN_SOURCE, _MAX_ERROR_LENGTH,
    _truncate_msg, RenderCommand,
)

if TYPE_CHECKING:
    from ..ui.events.event_types import DisplayEvent

# 事件处理器类型：接收 DisplayEvent，通过 push_cmd 入队
_EventHandler = Callable[["DisplayEvent"], None]


class EventDispatcher:
    """事件过滤+入队处理器。

    将 11 种 DisplayEvent 子类型过滤并转换为 RenderCommand 元组入队。
    与 ChatUIConsumer 解耦——仅依赖 push_cmd 回调，不感知队列实现细节。

    设计要点：
      - 每个 handler 先 isinstance 类型守卫，再过滤 label/source
      - handler 在 EventBus 回调线程中执行（非阻塞，仅过滤+入队）
      - SubAgent 事件通过 _is_agent_source 识别（source 前缀 "agent-"）
    """

    # ── 事件处理器注册表 ──
    _EVENT_HANDLERS: tuple[tuple[str, str], ...] = (
        ("ReasoningChunkEvent",    "_on_reasoning_chunk"),
        ("ContentChunkEvent",      "_on_content_chunk"),
        ("PhaseDoneEvent",         "_on_phase_done"),
        ("ToolStartedEvent",       "_on_tool_started"),
        ("ToolDoneEvent",          "_on_tool_done"),
        ("ToolOutputChunkEvent",   "_on_tool_output"),
        ("ToolSummaryEvent",       "_on_tool_summary"),
        ("ParseInfoEvent",         "_on_parse_info"),
        ("ParseInfoDoneEvent",     "_on_parse_info_done"),
        ("ModelPhaseEvent",        "_on_model_phase"),
        ("OutputEvent",            "_on_output"),
    )

    def __init__(self, push_cmd: Callable[[tuple], None]):
        self._push_cmd = push_cmd
        # 懒加载事件类型（仅在首次使用时 import）
        self._event_types: dict[str, type] = {}
        self._event_lock = threading.Lock()

    def _get_event_type(self, name: str) -> type:
        """惰性加载事件类型，避免 EventDispatcher 构造时 import 事件模块。

        使用双重检查锁定（double-checked locking）防止多 EventBus 回调线程
        同时首次访问时重复导入和 dict 写入。
        """
        # 首次检查（无锁路径，快速通过）
        cached = self._event_types.get(name)
        if cached is not None:
            return cached
        # 双重检查（加锁路径，仅首次加载时进入）
        with self._event_lock:
            cached = self._event_types.get(name)
            if cached is not None:
                return cached
            from ..ui.events.event_types import (
                ContentChunkEvent,
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
            self._event_types.update({
                "ReasoningChunkEvent": ReasoningChunkEvent,
                "ContentChunkEvent": ContentChunkEvent,
                "PhaseDoneEvent": PhaseDoneEvent,
                "ToolStartedEvent": ToolStartedEvent,
                "ToolDoneEvent": ToolDoneEvent,
                "ToolOutputChunkEvent": ToolOutputChunkEvent,
                "ToolSummaryEvent": ToolSummaryEvent,
                "ParseInfoEvent": ParseInfoEvent,
                "ParseInfoDoneEvent": ParseInfoDoneEvent,
                "ModelPhaseEvent": ModelPhaseEvent,
                "OutputEvent": OutputEvent,
            })
            return self._event_types[name]

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
        R = self._get_event_type("ReasoningChunkEvent")
        if not isinstance(event, R):
            return
        if event.label != _MAIN_LABEL or not event.text:
            return
        self._push_cmd((RenderCommand.REASONING, event.text))

    def _on_content_chunk(self, event: "DisplayEvent") -> None:
        R = self._get_event_type("ContentChunkEvent")
        if not isinstance(event, R):
            return
        if event.label != _MAIN_LABEL or not event.text:
            return
        self._push_cmd((RenderCommand.CONTENT, event.text))

    def _on_phase_done(self, event: "DisplayEvent") -> None:
        R = self._get_event_type("PhaseDoneEvent")
        if not isinstance(event, R):
            return
        if event.label != _MAIN_LABEL:
            return
        self._push_cmd((RenderCommand.PHASE_DONE, event.phase))

    def _on_tool_started(self, event: "DisplayEvent") -> None:
        R = self._get_event_type("ToolStartedEvent")
        if not isinstance(event, R):
            return
        if not self._is_agent_source(event.source):
            return
        self._push_cmd((RenderCommand.TOOL_COUNT_INC,))

    def _on_tool_done(self, event: "DisplayEvent") -> None:
        R = self._get_event_type("ToolDoneEvent")
        if not isinstance(event, R):
            return
        if not self._is_agent_source(event.source):
            return
        if not event.success:
            self._push_cmd((RenderCommand.TOOL_FAIL_INC,))
        else:
            self._push_cmd((RenderCommand.TOOL_COUNT_DEC,))

    def _on_tool_output(self, event: "DisplayEvent") -> None:
        R = self._get_event_type("ToolOutputChunkEvent")
        if not isinstance(event, R):
            return
        if not self._is_agent_source(event.source):
            return
        text = event.text.rstrip("\n")
        if text:
            self._push_cmd((RenderCommand.TOOL_OUTPUT, text))

    def _on_parse_info(self, event: "DisplayEvent") -> None:
        R = self._get_event_type("ParseInfoEvent")
        if not isinstance(event, R):
            return
        if not self._is_agent_source(event.source):
            return
        self._push_cmd((RenderCommand.PARSE_INFO, event.tool_names, event.tokens, event.elapsed))

    def _on_parse_info_done(self, event: "DisplayEvent") -> None:
        R = self._get_event_type("ParseInfoDoneEvent")
        if not isinstance(event, R):
            return
        if not self._is_agent_source(event.source):
            return
        self._push_cmd((RenderCommand.PARSE_INFO, "", _CLEAR_PARSE_LINE, 0.0))

    def _on_output(self, event: "DisplayEvent") -> None:
        R = self._get_event_type("OutputEvent")
        if not isinstance(event, R):
            return
        if not event.text:
            return
        if event.source == "cmd":
            self._push_cmd((RenderCommand.CMD_OUTPUT, event.text))
        else:
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
        R = self._get_event_type("ModelPhaseEvent")
        if not isinstance(event, R):
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
        R = self._get_event_type("ToolSummaryEvent")
        if not isinstance(event, R):
            return
        if not self._is_agent_source(event.source):
            return
        if not event.successful_tools and not event.failed_tools:
            return
        self._push_cmd((RenderCommand.TOOL_SUMMARY, event.successful_tools, event.failed_tools))
