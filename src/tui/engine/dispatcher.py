"""事件分发器 — DisplayEvent → RenderCommand 过滤+入队。

从 _tui.py 拆分，12 种事件类型映射到对应 RenderCommand。
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Callable

from .const import (
    RenderCommand,
    _CLEAR_PARSE_LINE,
)

from ..core.text_utils import truncate

if TYPE_CHECKING:
    from ..consumer.chat_config import ChatConfig
    from ..events.event_types import (
        DisplayEvent,
        ReasoningChunkEvent,
        ContentChunkEvent,
        PhaseDoneEvent,
        ToolDoneEvent,
        ToolParsingEvent,
        ToolOutputChunkEvent,
        ToolStartedEvent,
        ToolSummaryEvent,
        ParseInfoEvent,
        ParseInfoDoneEvent,
        OutputEvent,
        ModelPhaseEvent,
    )

from ..core.registry_base import RegistryBase
from ..events import event_types as _EVENT_TYPES
from ..framework import Framework


# ═══════════════════════════════════════════════════════════
# EventHandlerRegistry — 可注册事件映射表
# ═══════════════════════════════════════════════════════════

class EventHandlerRegistry(RegistryBase):
    """事件类型 → 处理器方法名的线程安全注册表。

    支持运行时动态注册/查询。
    使用模式参考 ComponentRegistry。

    特性：
    - register(event_type, handler_name) — 注册新映射
    - resolve(event_type) — 查找处理器方法名
    - list_registered() — 获取所有已注册映射的副本
    - register_defaults() — 注册 12 种默认事件映射
    """

    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
        self._entries: dict[type, str] = {}

    # ── 注册 ──────────────────────────────────────

    def register(self, event_type: type, handler_name: str) -> None:
        """注册事件类型到处理器方法名的映射。

        Args:
            event_type: DisplayEvent 子类
            handler_name: EventDispatcher 上对应 _on_* 方法的名字符串
        """
        with self._lock:
            self._entries[event_type] = handler_name

    # ── 查询 ──────────────────────────────────────

    def resolve(self, event_type: type) -> str | None:
        """查找事件类型对应的处理器方法名。

        Returns:
            方法名字符串，未注册返回 None。
        """
        with self._lock:
            return self._entries.get(event_type)

    def list_registered(self) -> dict[type, str]:
        """返回所有已注册映射的副本（线程安全）。"""
        with self._lock:
            return dict(self._entries)

    # ── 默认注册 ──────────────────────────────────

    def register_defaults(self) -> None:
        """注册默认的 12 种 DisplayEvent → 处理器方法映射。

        在 EventDispatcher.__init__ 中自动调用，保证向后兼容。
        """
        self.register(_EVENT_TYPES.ReasoningChunkEvent, "_on_reasoning_chunk")
        self.register(_EVENT_TYPES.ContentChunkEvent, "_on_content_chunk")
        self.register(_EVENT_TYPES.PhaseDoneEvent, "_on_phase_done")
        self.register(_EVENT_TYPES.ToolParsingEvent, "_on_tool_parsing")
        self.register(_EVENT_TYPES.ToolStartedEvent, "_on_tool_started")
        self.register(_EVENT_TYPES.ToolDoneEvent, "_on_tool_done")
        self.register(_EVENT_TYPES.ToolOutputChunkEvent, "_on_tool_output")
        self.register(_EVENT_TYPES.ParseInfoEvent, "_on_parse_info")
        self.register(_EVENT_TYPES.ParseInfoDoneEvent, "_on_parse_info_done")
        self.register(_EVENT_TYPES.OutputEvent, "_on_output")
        self.register(_EVENT_TYPES.ModelPhaseEvent, "_on_model_phase")
        self.register(_EVENT_TYPES.ToolSummaryEvent, "_on_tool_summary")


# ═══════════════════════════════════════════════════════════
# EventDispatcher
# ═══════════════════════════════════════════════════════════

class EventDispatcher:
    """DisplayEvent → RenderCommand 过滤+入队。

    将 12 种 DisplayEvent 类型映射到对应的 RenderCommand 并推入命令队列：

    - ReasoningChunkEvent  → REASONING       (推理内容块)
    - ContentChunkEvent    → CONTENT         (助手回答块)
    - PhaseDoneEvent       → PHASE_DONE      (推理/内容阶段完成)
    - ToolParsingEvent     → MAIN_PHASE      (工具参数解析中)
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

    def __init__(self, push_cmd: Callable[[tuple], None], config: ChatConfig | None = None):
        self._push_cmd = push_cmd
        self._config: ChatConfig | None = config
        # ── 从 TuiConfig 读取 max_error_length ──
        _tui_cfg = Framework.get_default().get_config()
        self._max_error_length = _tui_cfg.max_error_length
        # ── 可注册事件映射表 ──
        self._handler_registry = EventHandlerRegistry()
        self._handler_registry.register_defaults()
        self._custom_handlers: dict[type, Callable] = {}

    def _is_agent_source(self, source: str | None) -> bool:
        if source is None:
            return False
        if self._config:
            main_source = self._config.main_source
        else:
            from ..consumer.chat_config import ChatConfig
            main_source = ChatConfig.defaults().main_source
        return source == main_source or source.startswith("agent-")

    def _pre_filter(self, event: DisplayEvent, event_type, *, require_label=False, require_source=False) -> bool:
        """统一前置过滤：不满足条件返回 False（应跳过该事件）。

        取代各 handler 中重复的 isinstance/label/source 判断。
        """
        if not isinstance(event, event_type):
            return False
        if self._config:
            main_label = self._config.main_label
        else:
            from ..consumer.chat_config import ChatConfig
            main_label = ChatConfig.defaults().main_label
        if require_label and event.label != main_label:
            return False
        if require_source and not self._is_agent_source(event.source):
            return False
        return True

    # ── 公开 API：动态注册/查询 ──────────────────

    def register_handler(self, event_type: type, handler_method: Callable) -> None:
        """注册自定义事件处理器（供外部扩展）。

        注册的 callable 会在 list_handlers() 中与默认映射合并返回，
        签名需兼容 (event) -> None。

        Args:
            event_type: DisplayEvent 子类
            handler_method: 事件处理 callable，签名为 (event) -> None
        """
        self._custom_handlers[event_type] = handler_method

    def list_handlers(self) -> dict[type, Callable]:
        """返回所有已注册事件类型→处理器 callable 的映射。

        合并默认注册表（通过 getattr 解析方法名）和自定义处理器。
        consumer 使用此方法批量订阅事件总线。

        Returns:
            {event_type: callable} 映射字典
        """
        result: dict[type, Callable] = {}
        for event_type, handler_name in self._handler_registry.list_registered().items():
            result[event_type] = getattr(self, handler_name)
        result.update(self._custom_handlers)
        return result

    # ── 事件处理器 ────────────────────────────────

    def _on_reasoning_chunk(self, event: ReasoningChunkEvent) -> None:
        if not self._pre_filter(event, _EVENT_TYPES.ReasoningChunkEvent, require_label=True):
            return
        if not event.text:
            return
        self._push_cmd((RenderCommand.REASONING, event.text))

    def _on_content_chunk(self, event: ContentChunkEvent) -> None:
        if not self._pre_filter(event, _EVENT_TYPES.ContentChunkEvent, require_label=True):
            return
        if not event.text:
            return
        self._push_cmd((RenderCommand.CONTENT, event.text))

    def _on_tool_parsing(self, event: ToolParsingEvent) -> None:
        if not self._pre_filter(event, _EVENT_TYPES.ToolParsingEvent, require_source=True):
            return
        self._push_cmd((RenderCommand.MAIN_PHASE, "parsing"))

    def _on_phase_done(self, event: PhaseDoneEvent) -> None:
        if not self._pre_filter(event, _EVENT_TYPES.PhaseDoneEvent, require_label=True):
            return
        self._push_cmd((RenderCommand.PHASE_DONE, event.phase))

    def _on_tool_started(self, event: ToolStartedEvent) -> None:
        if not self._pre_filter(event, _EVENT_TYPES.ToolStartedEvent, require_source=True):
            return
        self._push_cmd((RenderCommand.TOOL_COUNT_INC,))

    def _on_tool_done(self, event: ToolDoneEvent) -> None:
        if not self._pre_filter(event, _EVENT_TYPES.ToolDoneEvent, require_source=True):
            return
        if not event.success:
            self._push_cmd((RenderCommand.TOOL_FAIL_INC,))
            self._push_cmd((RenderCommand.TOOL_COUNT_DEC,))
        else:
            self._push_cmd((RenderCommand.TOOL_COUNT_DEC,))

    def _on_tool_output(self, event: ToolOutputChunkEvent) -> None:
        if not self._pre_filter(event, _EVENT_TYPES.ToolOutputChunkEvent, require_source=True):
            return
        text = event.text.rstrip("\n")
        if text:
            self._push_cmd((RenderCommand.TOOL_OUTPUT, text))

    def _on_parse_info(self, event: ParseInfoEvent) -> None:
        if not self._pre_filter(event, _EVENT_TYPES.ParseInfoEvent, require_source=True):
            return
        self._push_cmd((RenderCommand.PARSE_INFO, event.tool_names, event.tokens, event.elapsed))

    def _on_parse_info_done(self, event: ParseInfoDoneEvent) -> None:
        if not self._pre_filter(event, _EVENT_TYPES.ParseInfoDoneEvent, require_source=True):
            return
        self._push_cmd((RenderCommand.PARSE_INFO, "", _CLEAR_PARSE_LINE, 0.0))

    def _on_output(self, event: OutputEvent) -> None:
        if not self._pre_filter(event, _EVENT_TYPES.OutputEvent):
            return
        if not event.text:
            return
        self._push_cmd((RenderCommand.WRITE_LINE, event.text))

    def _on_model_phase(self, event: ModelPhaseEvent) -> None:
        if not self._pre_filter(event, _EVENT_TYPES.ModelPhaseEvent, require_label=True):
            return
        # 非 error 阶段转发给底部栏显示状态（思考/回答/接收工具参数）
        if event.phase != "error":
            self._push_cmd((RenderCommand.MAIN_PHASE, event.phase))
            return
        if not event.info:
            return
        info = truncate(event.info, self._max_error_length, normalize=False, suffix="...")
        self._push_cmd((RenderCommand.ERROR, info))

    def _on_tool_summary(self, event: ToolSummaryEvent) -> None:
        if not self._pre_filter(event, _EVENT_TYPES.ToolSummaryEvent, require_source=True):
            return
        if not event.successful_tools and not event.failed_tools:
            return
        self._push_cmd((RenderCommand.TOOL_SUMMARY, event.successful_tools, event.failed_tools))
