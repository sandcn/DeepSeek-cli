"""ChatUI — 终端聊天消费者，订阅 DisplayEventBus 事件并渲染到终端。

架构：
  EventBus（事件线程） → EventDispatcher → 入队 RenderCommand
  RenderEngine（Reader 线程 10Hz） → 出队 RenderCommand → ContentRenderer → 终端 I/O

事件处理与终端 I/O 解耦：_on_* handler 只做过滤+入队（非阻塞），
Reader 线程串行消费所有渲染命令，保证输出有序。

内部子系统：
  EventDispatcher   — 事件订阅/过滤/入队（组合 11 种事件处理器）
  ContentRenderer   — 14 种渲染命令的纯渲染方法（在 Reader 线程调用）
  RenderEngine      — Reader 线程 + 命令队列 + 渲染循环
  _RenderState      — 渲染器生命周期管理（推理/内容/工具适配器）
  _CmplHandler      — Tab 补全交互逻辑
  ChatUIErrorHandler— 捕获 ERROR+ 级别日志并投递到 ChatUI 上屏

流式输出期间底部栏：
  _BottomBar 使用 ANSI 滚动区域（DECSTBM）将终端分为上下两部分——
  上方内容区（行 1..H-3）正常滚动，底部 3 行固定显示输入界面。
  底部栏刷新通过 output_lock 与内容输出串行化，避免竞态。
"""

from __future__ import annotations

# ── 公开 API ──────────────────────────────────────────
from ._consumer import ChatUIConsumer
from ._const import RenderCommand
from ._error_handler import ChatUIErrorHandler
from ._state import get_active_chat_ui

# ── 测试 / 内部模块访问（保持 backward compat） ──────
from ._completion import _apply_completion
from ._const import _MAIN_LABEL
from ._error_handler import _error_handler, _handler_reentrant

# ── 事件类型（测试通过 src.chat_ui 直接访问） ────────
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

# ── 模块级全局变量（供 import src.chat_ui as mod; mod._active_parallel_display = x） ──
from ._state import _active_consumer, _active_parallel_display

__all__ = [
    "ChatUIConsumer",
    "ChatUIErrorHandler",
    "RenderCommand",
    "get_active_chat_ui",
]
