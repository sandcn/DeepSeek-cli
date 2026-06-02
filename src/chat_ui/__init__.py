"""ChatUI — 终端聊天消费者包。

架构（拆分为 7 个子系统）：
  _const         — Layer 0：RenderCommand 枚举、Rich Style 常量、_ReasoningState
  _state         — Layer 0：全局状态（_active_consumer, _active_parallel_display）
  _error_handler — Layer 1：ChatUIErrorHandler 日志捕获+上屏投递
  _render_state  — Layer 1：_RenderState 渲染器生命周期管理
  _controls      — Layer 1：Control 基类 + 控件子类 + ControlList 控件列表管理
  _completion    — Layer 2：_CmplHandler Tab 补全交互 + _apply_completion
  _renderers     — Layer 2：ContentRenderer 14 种渲染命令执行
  _dispatcher    — Layer 2：EventDispatcher 事件订阅/过滤/入队（11 种事件处理器）
  _engine        — Layer 3：RenderEngine Reader 线程 + 命令队列 + 渲染循环
  _consumer      — Layer 4：ChatUIConsumer 外观类，组合所有子系统

公开 API（完全向后兼容）：
  ChatUIConsumer       — 终端聊天消费者
  get_active_chat_ui   — 获取活跃实例
  RenderCommand        — 渲染命令枚举
  ChatUIErrorHandler   — 日志→上屏投递
  _apply_completion    — Tab 补全应用（纯函数）
  _active_consumer     — 模块级活跃实例引用
  _active_parallel_display — 模块级 ParallelDisplay 引用
  _MAIN_LABEL          — 主 Agent 标签（供测试使用）
  Control              — 控件抽象基类
  ControlList          — 控件列表管理器
  TextControl          — 纯文本控件
  MarkdownControl      — 流式 Markdown 控件
  ToolOutputControl    — 工具输出控件
  ToolSummaryControl   — 工具汇总控件
  ParseInfoControl     — 解析进度控件
"""

from __future__ import annotations

# ── Layer 0 导出 ──────────────────────────────────────
from ._const import (
    RenderCommand,
    _MAIN_LABEL,
)
from ._state import (
    _active_consumer,
    _active_parallel_display,
    get_active_chat_ui,
)

# ── Layer 1 导出 ──────────────────────────────────────
# ★ 显式注册 ChatUIErrorHandler 到 root logger
#   在模块加载时立即生效（而非依赖 _consumer.py 的 side-effect import），
#   确保 ERROR+ 级别日志能投递到 ChatUI 上屏。
from ._error_handler import ChatUIErrorHandler, register_error_handler
register_error_handler()
from ._controls import (
    Control,
    ControlList,
    MarkdownControl,
    ParseInfoControl,
    TextControl,
    ToolOutputControl,
    ToolSummaryControl,
)

# ── Layer 2 导出 ──────────────────────────────────────
from ._completion import _apply_completion

# ── Layer 4 导出 ──────────────────────────────────────
from ._consumer import ChatUIConsumer

__all__ = [
    "ChatUIConsumer",
    "get_active_chat_ui",
    "RenderCommand",
    "ChatUIErrorHandler",
    "register_error_handler",
    "_apply_completion",
    "_active_consumer",
    "_active_parallel_display",
    "_MAIN_LABEL",
    "Control",
    "ControlList",
    "MarkdownControl",
    "ParseInfoControl",
    "TextControl",
    "ToolOutputControl",
    "ToolSummaryControl",
]
