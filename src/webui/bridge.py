"""WebEventBridge — 订阅 EventBus 事件并转发到 WebSocket

数据驱动的事件订阅注册，减少样板代码。
继承 BaseWebSocketSender 复用背压控制与安全发送能力。

演进说明：
  - 所有消息使用 types.py 中的 WSMsgType 常量和消息构建函数
  - 事件处理逻辑与消息构建解耦：builder 负责消息格式，handler 负责路由逻辑
"""

from __future__ import annotations

import logging
from typing import Callable

from ..tui.events.event_bus import DisplayEventBus
from ..tui.events.event_types import (
    AgentAddedEvent,
    AgentResultEvent,
    AgentStatusChanged,
    ContentChunkEvent,
    DisplayEvent,
    OutputEvent,
    PhaseDoneEvent,
    ReasoningChunkEvent,
    ToolOutputChunkEvent,
    ToolSummaryEvent,
    UserSelectNeededEvent,
    ToolParsingEvent,
    ToolStartedEvent,
    ToolDoneEvent,
    ToolBatchStartedEvent,
    ModelPhaseEvent,
    UsageUpdatedEvent,
)
from ._base_sender import BaseWebSocketSender
from .ws_handler.sandbox import FILE_MODIFY_TOOLS, build_sandbox_updated
from .types import (
    msg_content_chunk,
    msg_reasoning_chunk,
    msg_phase_done,
    msg_tool_output_chunk,
    msg_agent_tool_parsing,
    msg_agent_tool_started,
    msg_agent_tool_done,
    msg_user_select_needed,
    msg_agent_added,
    msg_tool_summary,
    msg_agent_status,
    msg_command_output,
    msg_tool_batch_start,
)

_logger = logging.getLogger(__name__)


class WebEventBridge(BaseWebSocketSender):
    """订阅 DisplayEventBus 事件，转发到前端 WebSocket。

    不阻塞事件循环 — 所有转发为 fire-and-forget。

    事件处理通过 _EVENT_BINDINGS 数据驱动注册，减少样板代码。
    新增事件类型只需在 _EVENT_BINDINGS 中添加映射即可。
    """

    def __init__(self, send_func: Callable[[dict], None], event_bus: DisplayEventBus | None = None,
                 select_id_tracker: set[str] | None = None):
        super().__init__(send_func)
        self._bus = event_bus or DisplayEventBus.get_default()
        self._handlers: list[tuple] = []
        self._agent_labels: set[str] = set()
        self._select_id_tracker = select_id_tracker

    # ═══════════════════════════════════════════════════════
    # select_id_tracker property（取代动态注入）
    # ═══════════════════════════════════════════════════════

    @property
    def select_id_tracker(self) -> set[str] | None:
        return self._select_id_tracker

    @select_id_tracker.setter
    def select_id_tracker(self, value: set[str] | None) -> None:
        self._select_id_tracker = value

    # ═══════════════════════════════════════════════════════
    # 数据驱动的事件绑定表
    # ═══════════════════════════════════════════════════════

    # (事件类型, 处理方法名) 元组列表
    _EVENT_BINDINGS: list[tuple[type, str]] = [
        (ContentChunkEvent, "_on_content_chunk"),
        (ReasoningChunkEvent, "_on_reasoning_chunk"),
        (PhaseDoneEvent, "_on_phase_done"),
        (ToolOutputChunkEvent, "_on_tool_output"),
        (UserSelectNeededEvent, "_on_user_select_needed"),
        (ToolSummaryEvent, "_on_tool_summary"),
        (AgentAddedEvent, "_on_agent_added"),
        (AgentStatusChanged, "_on_agent_status"),
        (ToolParsingEvent, "_on_subagent_tool_event"),
        (ToolStartedEvent, "_on_subagent_tool_event"),
        (ToolDoneEvent, "_on_subagent_tool_event"),
        (ModelPhaseEvent, "_on_subagent_phase_event"),
        (UsageUpdatedEvent, "_on_subagent_usage_event"),
        (AgentResultEvent, "_on_agent_result"),
        (ToolBatchStartedEvent, "_on_tool_batch_start"),
        (OutputEvent, "_on_output_event"),
    ]

    # ── 订阅/取消订阅（数据驱动）─────────────────────────

    def subscribe(self) -> None:
        """订阅所有流式事件（通过 _EVENT_BINDINGS 数据驱动注册）。"""
        for event_type, method_name in self._EVENT_BINDINGS:
            handler = getattr(self, method_name)
            self._bus.subscribe(handler, event_type=event_type)
            self._handlers.append((handler, event_type))
        _logger.debug("WebEventBridge.subscribe: 已注册 %d 个事件处理器", len(self._handlers))

    def unsubscribe(self) -> None:
        """取消所有订阅。"""
        for handler, event_type in self._handlers:
            try:
                self._bus.unsubscribe(handler, event_type=event_type)
            except Exception:
                _logger.exception("取消订阅异常: %s", event_type)
        self._handlers.clear()

    # ═══════════════════════════════════════════════════════
    # 事件处理 — 使用 types.py 消息构建函数
    # ═══════════════════════════════════════════════════════

    def _on_content_chunk(self, event: DisplayEvent) -> None:
        # 跳过 subagent 的内容块 — subagent 的 thinking/answer 不单独创建气泡
        if isinstance(event, ContentChunkEvent) and event.text and event.label not in self._agent_labels:
            self.send_json(msg_content_chunk(event.text, event.label))

    def _on_reasoning_chunk(self, event: DisplayEvent) -> None:
        # 跳过 subagent 的推理块 — subagent 的 thinking/answer 不单独创建气泡
        if isinstance(event, ReasoningChunkEvent) and event.text and event.label not in self._agent_labels:
            self.send_json(msg_reasoning_chunk(event.text, event.label))

    def _on_phase_done(self, event: DisplayEvent) -> None:
        # 跳过 subagent 的阶段完成事件 — subagent 不触发气泡更新
        if isinstance(event, PhaseDoneEvent) and event.label not in self._agent_labels:
            self.send_json(msg_phase_done(event.phase, event.label))

    def _on_tool_output(self, event: DisplayEvent) -> None:
        if isinstance(event, ToolOutputChunkEvent) and event.text:
            self.send_json(msg_tool_output_chunk(event.label, event.text))

    def _on_user_select_needed(self, event: DisplayEvent) -> None:
        if isinstance(event, UserSelectNeededEvent):
            if self._select_id_tracker is not None:
                self._select_id_tracker.add(event.select_id)
            self.send_json(msg_user_select_needed(
                select_id=event.select_id,
                title=event.title,
                options=list(event.options),
                multi_select=event.multi_select,
                default_options=list(event.default_options),
                timeout=event.timeout,
            ))

    def _on_agent_added(self, event: DisplayEvent) -> None:
        if isinstance(event, AgentAddedEvent):
            self._agent_labels.add(event.label)
            self.send_json(msg_agent_added(
                label=event.label,
                description=event.description,
                status=event.status,
                source=event.source,
                dispatch_label=event.dispatch_label,
            ))

    def _on_tool_summary(self, event: DisplayEvent) -> None:
        if isinstance(event, ToolSummaryEvent):
            self.send_json(msg_tool_summary(
                successful_tools=list(event.successful_tools),
                failed_tools=list(event.failed_tools),
            ))

    def _on_agent_status(self, event: DisplayEvent) -> None:
        if isinstance(event, AgentStatusChanged):
            if event.status in ("done", "fail", "error"):
                self._agent_labels.discard(event.label)
            self.send_json(msg_agent_status(event.label, event.status))

    # ── Sub-Agent 工具事件 ────────────────────────────────

    def _on_subagent_tool_event(self, event: DisplayEvent) -> None:
        """转发 sub-agent 的工具事件到前端（复用 types.py 构建函数 + agent_label 标识）。"""
        # ★ P0 修复: 沙盒更新推送不因 source 过滤短路。
        #   文件修改工具（不论来源）完成后必须推送最新沙盒计数。
        if isinstance(event, ToolDoneEvent) and event.tool_name in FILE_MODIFY_TOOLS:
            self.send_json(build_sandbox_updated())

        if event.source not in self._agent_labels:
            return

        msg_type = type(event)
        agent_label = event.source

        if msg_type == ToolParsingEvent:
            msg = msg_agent_tool_parsing(agent_label, event.tool_name, getattr(event, 'arguments', ''))
        elif msg_type == ToolDoneEvent:
            msg = msg_agent_tool_done(agent_label, event.tool_name, getattr(event, 'success', True))
        elif msg_type == ToolStartedEvent:
            msg = msg_agent_tool_started(agent_label, event.tool_name, getattr(event, 'detail', ''))
        else:
            _logger.warning("未知工具事件类型: %s，降级为 tool_parsing", msg_type)
            msg = msg_agent_tool_parsing(agent_label, getattr(event, 'tool_name', ''), getattr(event, 'arguments', ''))

        # 传递 tool_id 供前端精确匹配
        if hasattr(event, 'tool_id') and event.tool_id:
            msg["tool_id"] = event.tool_id

        self.send_json(msg)

    def _on_subagent_phase_event(self, event: DisplayEvent) -> None:
        """转发 sub-agent 的模型阶段事件到前端。"""
        if not isinstance(event, ModelPhaseEvent):
            return
        if event.source not in self._agent_labels:
            return
        self.send_json({
            "type": "agent_phase",
            "agent_label": event.source,
            "phase": event.phase,
            "info": event.info,
        })

    def _on_subagent_usage_event(self, event: DisplayEvent) -> None:
        """转发 sub-agent 的用量事件到前端。"""
        if not isinstance(event, UsageUpdatedEvent):
            return
        if event.source not in self._agent_labels:
            return
        self.send_json({
            "type": "agent_usage",
            "agent_label": event.source,
            "usage": event.usage,
        })

    def _on_agent_result(self, event: DisplayEvent) -> None:
        """转发 sub-agent 执行结果到前端。"""
        if not isinstance(event, AgentResultEvent):
            return
        self.send_json({
            "type": "agent_result",
            "agent_label": event.label,
            "description": event.description,
            "result": event.result,
            "error": event.error,
        })

    def _on_tool_batch_start(self, event: DisplayEvent) -> None:
        """转发批量工具开始事件到前端。"""
        if isinstance(event, ToolBatchStartedEvent):
            self.send_json(msg_tool_batch_start(event.label, list(event.tool_names)))

    def _on_output_event(self, event: DisplayEvent) -> None:
        """转发命令输出事件到前端。"""
        if isinstance(event, OutputEvent) and event.text:
            self.send_json(msg_command_output(event.text, event.level))


__all__ = ["WebEventBridge"]
