"""事件分发模块 — EventDispatcher DisplayEvent→RenderCommand 过滤+入队。

独立模块（2026-08-01 ink 重构：从 _renderer/ 迁出），ChatConfig 依赖替换为
filter_fn 注入。``push_cmd`` 由装配层注入（InkSession.push_cmd）。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

from src.tui._const import (
    RenderCmd,
    ReasoningCmd, ContentCmd, PhaseDoneCmd,
    ToolOutputCmd, ToolSummaryCmd, ToolOpenCmd, ToolCloseCmd,
    UserMsgCmd, ParseInfoCmd,
    NotificationCmd, WriteLineCmd,
    ToolCountIncCmd, ToolFailIncCmd, ErrorCmd, ToolCountDecCmd,
    SubagentFrameCmd, MainPhaseCmd,
    _CLEAR_PARSE_LINE,
    is_agent_source,
    truncate_error_message,
)
from src.tui._config import TuiConfig
from src.tui.events.event_types import DisplayEvent

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

    方向D 步骤7（2026-07-31）：订阅声明式化——
    ``list_handlers()`` 结果缓存（内置 12 类 + ``_handler_groups`` 声明式订阅组
    + ``_custom_handlers`` 自定义），``register_group`` 提供声明式订阅表入口；
    返回的 dict 为缓存对象，调用方（如 _lifecycle）只迭代不修改。
    """

    def __init__(
        self,
        push_cmd: Callable[[RenderCmd], None],
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
        self._max_error_length = (
            max_error_length if max_error_length is not None
            else TuiConfig.defaults().max_error_length
        )
        self._custom_handlers: dict[type, Callable[..., None]] = {}
        # 声明式订阅组注册表（方向D 步骤7）：register_group 存储事件类型 → 处理器映射
        self._handler_groups: dict[str, dict[type, Callable]] = {}
        # list_handlers 结果缓存：register_handler / register_group 后置 None 失效重建
        self._handlers_cache: dict[type, Callable] | None = None

    @staticmethod
    def _default_filter_fn(source: str | None) -> bool:
        """默认 source 过滤函数（收敛至 _const.is_agent_source 真源）。"""
        return is_agent_source(source)

    def _is_agent_source(self, source: str | None) -> bool:
        if source is None:
            return False
        return self._filter_fn(source)

    def _is_main_label(self, label: str | None) -> bool:
        return label == self._main_label

    def register_handler(self, event_type: type, handler_method: Callable) -> None:
        # P2-9：类型校验对齐 DisplayEventBus.subscribe（非 DisplayEvent 子类抛 TypeError）
        if not issubclass(event_type, DisplayEvent):
            raise TypeError(
                f"event_type 必须是 DisplayEvent 的子类，收到: {event_type}"
            )
        self._custom_handlers[event_type] = handler_method
        self._handlers_cache = None  # 缓存失效，下次 list_handlers() 重建

    def register_group(self, name: str, mapping: dict[type, Callable]) -> None:
        """注册声明式订阅组（方向D 步骤7）。

        Args:
            name: 订阅组名称（唯一标识，重复注册覆盖同组映射）。
            mapping: 事件类型 → 处理器方法映射。

        Raises:
            TypeError: mapping 中存在非 DisplayEvent 子类的键（P2-9 对齐
                DisplayEventBus.subscribe 校验）。
        """
        # P2-9：mapping 键类型校验（对齐 DisplayEventBus.subscribe）
        for mapping_key in mapping:
            if not issubclass(mapping_key, DisplayEvent):
                raise TypeError(
                    f"register_group {name!r} 的映射键必须是 DisplayEvent "
                    f"的子类，收到: {mapping_key}"
                )
        self._handler_groups[name] = mapping
        self._handlers_cache = None  # 缓存失效，下次 list_handlers() 重建

    def list_handlers(self) -> dict[type, Callable]:
        """返回事件类型 → 处理器映射（结果缓存，只读使用）。

        返回的 dict 为内部缓存对象，调用方应只读使用（勿修改，避免污染缓存）。
        register_handler / register_group 后缓存失效，下次调用重新构建。

        P3-9 并发说明：``_handlers_cache`` 无锁缓存——**注册仅在启动阶段
        单线程执行**（TuiAssembly 装配 + _lifecycle.start 订阅），运行期
        无动态注册调用方；读端（list_handlers）也仅在 start 阶段调用。
        若未来引入运行期动态注册须改用 RLock 保护。
        """
        if self._handlers_cache is not None:
            return self._handlers_cache
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
        for group in self._handler_groups.values():
            result.update(group)
        result.update(self._custom_handlers)
        self._handlers_cache = result
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
        # 主 agent 工具 → 上屏 box；subagent 工具 → 仅计数（面板自渲染）
        if event.source == "agent":
            tool_id = event.tool_id or event.label
            self._push_cmd(ToolOpenCmd(
                tool_name=event.tool_name, tool_id=tool_id, detail=event.detail,
            ))
        self._push_cmd(ToolCountIncCmd())

    def _on_tool_done(self, event: "ToolDoneEvent") -> None:
        if not self._is_agent_source(event.source) and not self._is_subagent_label(event.label):
            return
        if event.source == "agent":
            tool_id = event.tool_id or event.label
            self._push_cmd(ToolCloseCmd(tool_id=tool_id, success=event.success))
        if not event.success:
            self._push_cmd(ToolFailIncCmd())
            self._push_cmd(ToolCountDecCmd())
        else:
            self._push_cmd(ToolCountDecCmd())

    def _on_tool_output(self, event: "ToolOutputChunkEvent") -> None:
        if not self._is_agent_source(event.source):
            return
        # 仅主 agent 工具输出进入主内容 box；subagent 输出由面板自渲染
        if event.source != "agent":
            return
        text = event.text.rstrip("\n")
        if text:
            self._push_cmd(ToolOutputCmd(text=text, tool_id=event.label))

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
        message = truncate_error_message(event.info, self._max_error_length)
        self._push_cmd(ErrorCmd(message=message))

    def _on_tool_summary(self, event: "ToolSummaryEvent") -> None:
        if not self._is_agent_source(event.source):
            return
        if not event.successful_tools and not event.failed_tools:
            return
        self._push_cmd(ToolSummaryCmd(successful=event.successful_tools, failed=event.failed_tools))


__all__ = ["EventDispatcher"]
