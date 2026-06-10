"""ChatUI — 终端聊天消费者包。

架构（拆分为 4 层单向依赖 L0→L1→L2→L3）：

  Layer 0（常量+状态）：
    _const         — RenderCommand 枚举、Rich Style 常量、_ReasoningState 状态机
    _state         — 全局活跃实例引用
    _utils         — 通用工具函数
    _reentrant     — 线程本地重入保护

  Layer 1（基础设施）：
    _error_handler — ChatUIErrorHandler 日志捕获+上屏投递（模块级注册到 root logger）
    _render_state  — _RenderState 推理/内容渲染器生命周期管理（直接使用 IncrementalRenderer）

  Layer 2（业务逻辑）：
    _completion    — _CmplHandler Tab 补全交互 + _apply_completion 纯函数
    _renderers     — ContentRenderer 渲染命令 O(1) 字典分发，直接通过 OutputAdapter 打印
    _dispatcher    — EventDispatcher 11 种 DisplayEvent 过滤+入队（回调解耦队列）

  Layer 3（引擎）：
    _engine        — RenderEngine render 线程 + Queue 命令队列 + 渲染循环

  Layer 4（外观）：
    _consumer      — ChatUIConsumer 外观类，组合所有子系统

2026-06-10 架构简化：
  - 移除完整 Control 控件体系（Control/TextControl/MarkdownControl/ControlList等7个类）
  - ContentRenderer 直接通过 OutputAdapter 或 sys.__stdout__ 打印
  - 移除 _active_subagent_panel 全局引用
  - 移除 RenderCommand.SUBAGENT_REFRESH

公开 API：
  ChatUIConsumer       — 终端聊天消费者
  get_active_chat_ui   — 获取活跃实例
  RenderCommand        — 渲染命令枚举
  ChatUIErrorHandler   — 日志→上屏投递
  _apply_completion    — Tab 补全应用（纯函数）
  _active_consumer     — 模块级活跃实例引用
  _MAIN_LABEL          — 主 Agent 标签（供测试使用）
"""

from __future__ import annotations

import logging

# ── Layer 0 导出 ──────────────────────────────────────
from ._const import (
    RenderCommand,
    _MAIN_LABEL,
)
from ._state import (
    _active_consumer,
    get_active_chat_ui,
)

# ── Layer 1 导出 ──────────────────────────────────────
# ★ 显式注册 ChatUIErrorHandler 到 root logger（import 即生效）
#   在 __init__.py 中显式执行 addHandler，而非依赖模块级副作用。
from ._error_handler import ChatUIErrorHandler
logging.getLogger().addHandler(ChatUIErrorHandler())

# ── Layer 2 导出 ──────────────────────────────────────
from ._completion import _apply_completion

# ── Layer 4 导出 ──────────────────────────────────────
from ._consumer import ChatUIConsumer

__all__ = [
    "ChatUIConsumer",
    "get_active_chat_ui",
    "RenderCommand",
    "ChatUIErrorHandler",
    "_apply_completion",
    "_active_consumer",
    "_MAIN_LABEL",
]
