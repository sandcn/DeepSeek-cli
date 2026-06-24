"""ChatUI — 终端聊天消费者包。

组件化 TUI 架构：

  _components.py — 组件层
    ├── BottomBarProtocol / TuiComponent (基类)
    ├── UserMsgBlock / ThinkingBlock / AnswerBlock
    ├── ToolOutputBlock / ToolSummaryBlock
    ├── ErrorBlock / NotificationBlock / WriteLineBlock
    └── StatusLine / InputLine / CompletionPopup / SelectionMenu (@dataclass 数据模型)
  _renderer.py  — 渲染器（TuiRenderer + _RENDER_DISPATCH 命令分发表）
    ├── _RenderState  — IncrementalRenderer 生命周期管理
    ├── _RENDER_DISPATCH — 渲染命令分发表
    └── TuiRenderer     — 组件化渲染分发
  _engine.py    — 渲染引擎
    └── TuiEngine  — render 线程 + 命令队列 + 三阶段流水线
  _dispatcher.py — 事件分发器
    ├── _HANDLER_MAP     — 事件类型映射表
    └── EventDispatcher  — DisplayEvent → RenderCommand
  _consumer.py  — 消费者 API
    └── ChatUIConsumer  — 对外公开 API

基础设施模块：
  _ansi         — ANSI 转义序列工具（16色/样式/光标）
  _styled       — StyledText 纯 Python 样式化文本
  _const         — RenderCommand 枚举、ANSI 样式常量
  _state         — 全局活跃实例引用 + 引用计数
  _utils         — 通用工具函数
  _error_handler — 日志→上屏投递
  _completion    — _apply_completion 纯函数 + _CmplHandler

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

# ── 常量导出 ──────────────────────────────────────
from ._const import (
    RenderCommand,
    _MAIN_LABEL,
)

# ── 全局状态导出 ──────────────────────────────────
from ._state import (
    _active_consumer,
    get_active_chat_ui,
    is_error_handler_registered,
    set_error_handler_registered,
    get_error_handler_lock,
)

# ── 错误处理 ──────────────────────────────────────
from ._error_handler import ChatUIErrorHandler


def setup_chat_ui_error_handler() -> None:
    """显式注册 ChatUIErrorHandler 到 root logger。

    替代此前 __init__.py 导入时的隐式副作用。
    幂等操作——重复调用不重复注册。
    """
    with get_error_handler_lock():
        if is_error_handler_registered():
            return
        logging.getLogger().addHandler(ChatUIErrorHandler())
        set_error_handler_registered(True)

# ── 补全纯函数 ────────────────────────────────────
from ._completion import _apply_completion

# ── 核心 TUI（组件化架构） ─────────────────────────
from ._consumer import ChatUIConsumer

__all__ = [
    "ChatUIConsumer",
    "get_active_chat_ui",
    "RenderCommand",
    "ChatUIErrorHandler",
    "_apply_completion",
    "_active_consumer",
    "_MAIN_LABEL",
    "setup_chat_ui_error_handler",
    "_lazy_import_react_ink",
]


def _lazy_import_react_ink() -> dict:
    """惰性导入 react_ink 子包的所有公共 API 符号。

    用途：供外部模块（如 ChatUIConsumer / TuiEngine）在运行时按需获取
    React Ink 的公共 API，避免在模块导入时就加载整个 react_ink 子包。

    调用方：
    - src/chat_ui/_consumer.py：启用 React Ink 模式时导入 hooks/组件
    - src/chat_ui/_engine.py：启动 AnimationClock 时导入动画 API
    - 外部脚本/测试：通过 ChatUIConsumer 间接调用

    仅在环境变量 CHAT_UI_USE_REACT_LIKE 启用时返回非空 dict，
    否则返回空 dict（零开销，不触发任何 react_ink 模块加载）。

    Returns:
        包含所有 react_ink 公共 API 符号的 dict（键为符号名，值为符号对象），
        或空 dict（未启用时）。
    """
    from src.chat_ui.react_ink import _is_enabled
    if _is_enabled():
        from src.chat_ui.react_ink import (
            use_state, use_effect, use_ref, use_memo, use_callback,
            use_context, use_reducer, create_context,
            use_focus, use_focus_manager, FocusManager,
            Box, BoxBorderStyle,
            use_animation, AnimationClock,
            Static, Transform,
            FlexLayout, FlexStyle,
            ErrorBoundary, debug_component_tree, inspect_hooks,
        )
        _api = dict(locals())  # 复制 locals() 避免自引用问题
        _api.pop("_is_enabled", None)  # 排除 Feature Flag 函数本身
        return _api
    return {}

