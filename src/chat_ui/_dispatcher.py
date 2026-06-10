"""chat_ui 事件分发模块 — EventBus 事件过滤+入队。

Layer 2 — 依赖 _const（RenderCommand + _MAIN_SOURCE + _MAIN_LABEL）。
通过 _push_cmd 回调将渲染命令写入队列，与 _consumer 解耦。
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Callable

from ._const import (
    _CLEAR_PARSE_LINE, _MAIN_LABEL, _MAIN_SOURCE, _MAX_ERROR_LENGTH,
    RenderCommand,
)
from ._utils import _truncate_msg

if TYPE_CHECKING:
    from ..ui.events.event_types import DisplayEvent

# ── 事件类型模块路径（供反射加载使用） ──
_EVENT_MODULE = "..ui.events.event_types"

# 事件处理器类型：接收 DisplayEvent，通过 push_cmd 入队
_EventHandler = Callable[["DisplayEvent"], None]

# ── 事件处理器注册表（由 @event_handler 装饰器自动填充） ──
# 替代原来硬编码的 _EVENT_HANDLERS 元组，消除字符串双重维护。
_event_handler_registry: dict[str, str] = {}


def event_handler(event_type_name: str):
    """装饰器：将实例方法注册为 DisplayEvent 事件处理器。

    用法:
        @event_handler("ReasoningChunkEvent")
        def _on_reasoning_chunk(self, event):
            ...

    装饰器将事件类型名与方法名注册到 _event_handler_registry，
    _get_event_type() 通过此注册表惰性加载事件类型。
    添加新事件类型只需在对应方法上加装饰器，消除双重维护。
    """
    def decorator(method):
        _event_handler_registry[event_type_name] = method.__name__
        return method
    return decorator


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
        # 懒加载事件类型（仅在首次使用时 import）
        self._event_types: dict[str, type] = {}
        self._event_types_loaded: bool = False

    def _get_event_type(self, name: str) -> type:
        """惰性加载事件类型（首次调用时一次性加载全部 11 种）。

        通过 `importlib.import_module` 从 _event_handler_registry 的名称列表
        自动反射加载，消除手工 import 块的双重维护。

        首次调用一次性装载全部事件类型，
        后续直接通过 self._event_types[name] 访问。
        若 name 不在注册表中，返回 object 使 isinstance 安全返回 False。
        """
        if not self._event_types_loaded:
            mod = importlib.import_module(_EVENT_MODULE, package=__package__)
            self._event_types = {
                name: getattr(mod, name, object)
                for name in _event_handler_registry
            }
            self._event_types_loaded = True
        return self._event_types.get(name, object)

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

    @event_handler("ReasoningChunkEvent")
    def _on_reasoning_chunk(self, event: "DisplayEvent") -> None:
        R = self._get_event_type("ReasoningChunkEvent")
        if not isinstance(event, R):
            return
        if event.label != _MAIN_LABEL or not event.text:
            return
        self._push_cmd((RenderCommand.REASONING, event.text))

    @event_handler("ContentChunkEvent")
    def _on_content_chunk(self, event: "DisplayEvent") -> None:
        R = self._get_event_type("ContentChunkEvent")
        if not isinstance(event, R):
            return
        if event.label != _MAIN_LABEL or not event.text:
            return
        self._push_cmd((RenderCommand.CONTENT, event.text))

    @event_handler("PhaseDoneEvent")
    def _on_phase_done(self, event: "DisplayEvent") -> None:
        R = self._get_event_type("PhaseDoneEvent")
        if not isinstance(event, R):
            return
        if event.label != _MAIN_LABEL:
            return
        self._push_cmd((RenderCommand.PHASE_DONE, event.phase))

    @event_handler("ToolStartedEvent")
    def _on_tool_started(self, event: "DisplayEvent") -> None:
        R = self._get_event_type("ToolStartedEvent")
        if not isinstance(event, R):
            return
        if not self._is_agent_source(event.source):
            return
        self._push_cmd((RenderCommand.TOOL_COUNT_INC,))

    @event_handler("ToolDoneEvent")
    def _on_tool_done(self, event: "DisplayEvent") -> None:
        R = self._get_event_type("ToolDoneEvent")
        if not isinstance(event, R):
            return
        if not self._is_agent_source(event.source):
            return
        if not event.success:
            self._push_cmd((RenderCommand.TOOL_FAIL_INC,))
            self._push_cmd((RenderCommand.TOOL_COUNT_DEC,))
        else:
            self._push_cmd((RenderCommand.TOOL_COUNT_DEC,))

    @event_handler("ToolOutputChunkEvent")
    def _on_tool_output(self, event: "DisplayEvent") -> None:
        R = self._get_event_type("ToolOutputChunkEvent")
        if not isinstance(event, R):
            return
        if not self._is_agent_source(event.source):
            return
        text = event.text.rstrip("\n")
        if text:
            self._push_cmd((RenderCommand.TOOL_OUTPUT, text))

    @event_handler("ParseInfoEvent")
    def _on_parse_info(self, event: "DisplayEvent") -> None:
        R = self._get_event_type("ParseInfoEvent")
        if not isinstance(event, R):
            return
        if not self._is_agent_source(event.source):
            return
        self._push_cmd((RenderCommand.PARSE_INFO, event.tool_names, event.tokens, event.elapsed))

    @event_handler("ParseInfoDoneEvent")
    def _on_parse_info_done(self, event: "DisplayEvent") -> None:
        R = self._get_event_type("ParseInfoDoneEvent")
        if not isinstance(event, R):
            return
        if not self._is_agent_source(event.source):
            return
        self._push_cmd((RenderCommand.PARSE_INFO, "", _CLEAR_PARSE_LINE, 0.0))

    @event_handler("OutputEvent")
    def _on_output(self, event: "DisplayEvent") -> None:
        R = self._get_event_type("OutputEvent")
        if not isinstance(event, R):
            return
        if not event.text:
            return
        # ★ 所有 OutputEvent 统一走 WRITE_LINE（CMD_OUTPUT 已废弃）
        self._push_cmd((RenderCommand.WRITE_LINE, event.text))

    @event_handler("ModelPhaseEvent")
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

    @event_handler("ToolSummaryEvent")
    def _on_tool_summary(self, event: "DisplayEvent") -> None:
        R = self._get_event_type("ToolSummaryEvent")
        if not isinstance(event, R):
            return
        if not self._is_agent_source(event.source):
            return
        if not event.successful_tools and not event.failed_tools:
            return
        self._push_cmd((RenderCommand.TOOL_SUMMARY, event.successful_tools, event.failed_tools))
