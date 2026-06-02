"""ChatUI — 终端聊天消费者包。

架构（拆分为 10 个子系统，严格 5 层单向依赖 L0→L1→L2→L3→L4）：

  Layer 0（常量+状态）：
    _const         — RenderCommand 枚举、Rich Style 常量、_ReasoningState 状态机
    _state         — 全局活跃实例引用、线程本地重入保护

  Layer 1（基础设施）：
    _error_handler — ChatUIErrorHandler 日志捕获+上屏投递（模块级注册到 root logger）
    _render_state  — _RenderState 推理/内容渲染器生命周期管理
    _controls      — Control(ABC) → TextControl/MarkdownControl/… + ControlList

  Layer 2（业务逻辑）：
    _completion    — _CmplHandler Tab 补全交互 + _apply_completion 纯函数
    _renderers     — ContentRenderer 14 种渲染命令 O(1) 字典分发执行
    _dispatcher    — EventDispatcher 11 种 DisplayEvent 过滤+入队（回调解耦队列）

  Layer 3（引擎）：
    _engine        — RenderEngine Reader 线程 + Queue 命令队列 + 三阶段流水线

  Layer 4（外观）：
    _consumer      — ChatUIConsumer 外观类，组合所有子系统

2026-06-02 架构改进：
  - P0-1: resize 检测从 ContentRenderer 迁移到 RenderEngine._check_resize()
  - P0-2: ContentRenderer 控件创建提取 _create_controls() 工厂方法
  - P0-3: _phase_refresh_panels() 接收 pd 参数消除重复惰性导入
  - P1-1: ChatUIConsumer 新增 bottom_bar property，底部栏 API 收敛
  - P1-2: ContentRenderer 14 种 _do_* 命令补全系统测试
  - P1-3: MarkdownControl.refresh_width / reopen_reasoning 完整路径 / ToolOutputControl ANSI+\\r 盲区测试

公开 API：
  ChatUIConsumer       — 终端聊天消费者
  get_active_chat_ui   — 获取活跃实例
  ChatUIConsumer.bottom_bar — 底部栏对象属性
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

import logging

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
# ★ 显式注册 ChatUIErrorHandler 到 root logger（import 即生效）
#   在 __init__.py 中显式执行 addHandler，而非依赖模块级副作用。
from ._error_handler import ChatUIErrorHandler
logging.getLogger().addHandler(ChatUIErrorHandler())
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
