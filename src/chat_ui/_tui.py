"""兼容重导出层 — 从 5 个子模块重导出所有符号。

此文件将在 1-2 个版本后移除，调用方应直接 import 子模块。
子模块：
  _components.py — 组件层（TuiComponent + 11 个子类 + BottomBarProtocol）
  _renderer.py  — 渲染器（_RenderState + TuiRenderer + _RENDER_DISPATCH）
  _engine.py    — 渲染引擎（TuiEngine + 渲染线程）
  _dispatcher.py — 事件分发器（EventDispatcher + _HANDLER_MAP）
  _consumer.py  — 消费者 API（ChatUIConsumer）
"""

from __future__ import annotations

# 组件层
from ._components import (
    BottomBarProtocol,
    TuiComponent,
    UserMsgBlock,
    ThinkingBlock,
    AnswerBlock,
    ToolOutputBlock,
    ToolSummaryBlock,
    ErrorBlock,
    NotificationBlock,
    WriteLineBlock,
    StatusLine,
    InputLine,
    CompletionPopup,
    SelectionMenu,
)

# 渲染器
from ._renderer import (
    _RenderState,
    _estimate_content_lines,
    _RENDER_DISPATCH,
    TuiRenderer,
)

# 渲染引擎
from ._engine import (
    _ACTIVE_RENDER_INTERVAL,
    _IDLE_DRAIN_THRESHOLD,
    _CONSECUTIVE_FULL_THRESHOLD,
    TuiEngine,
)

# 事件分发器
from ._dispatcher import (
    _HANDLER_MAP,
    EventDispatcher,
)

# 消费者 API
from ._consumer import (
    ChatUIConsumer,
    RenderEngine,
    ContentRenderer,
)
