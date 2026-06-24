"""React Ink — 声明式终端 UI 框架子包。

提供 React Ink 风格的声明式终端 UI 能力，包括：

  _hooks.py      — Hooks 运行时（use_state / use_effect / use_ref / use_memo / use_callback / use_context / use_reducer）
  _focus.py      — 焦点管理系统（use_focus / use_focus_manager / Tab/Shift+Tab 焦点遍历）
  _box.py        — Box 边框组件（8 种边框样式 / 每边独立颜色 / backgroundColor 填充）
  _animation.py  — 动画系统（use_animation / AnimationClock / interval 帧间隔）
  _static.py     — Static 组件（key-based 累加渲染）
  _transform.py  — Transform 组件（ANSI 感知字符串变换）
  _layout.py     — Flexbox 布局引擎（纯 Python CSS Flexbox 子集）
  _devtools.py   — 开发者工具（组件树调试 / Hooks 检查 / ErrorBoundary）
  _types.py      — 共享类型定义（HookState / LayoutBox 等 dataclass）

所有新特性通过环境变量 CHAT_UI_USE_REACT_LIKE 门控，设为非空值启用。
"""

from __future__ import annotations

import os

# ── 子模块导入（按依赖顺序） ──
from ._layout import FlexLayout, FlexStyle
from ._box import Box, BORDER_STYLES as BoxBorderStyle
from ._animation import use_animation, AnimationClock
from ._static import Static
from ._transform import Transform
from ._types import HookState, HookError, LayoutBox, LayoutError
from ._devtools import ErrorBoundary, debug_component_tree, inspect_hooks

# Message Blocks（声明式 Box 包装组件）
from ._message_blocks import (
    ThinkingBlockBox,
    AnswerBlockBox,
    UserMsgBlockBox,
    ToolOutputBlockBox,
    ErrorBlockBox,
    NotificationBlockBox,
    TextContent,
    create_message_box,
)


def _is_enabled() -> bool:
    """检测是否启用 React Ink 特性。

    通过检查环境变量 CHAT_UI_USE_REACT_LIKE 决定是否启用新特性。
    遵循项目约定：仅当值为 "1"/"true"/"yes"/"on" 时启用，
    "0"/"false"/"no"/"off"/空字符串/未设置均为禁用。

    Returns:
        True 当 CHAT_UI_USE_REACT_LIKE 为启用的真值。
    """
    return os.environ.get("CHAT_UI_USE_REACT_LIKE", "").strip().lower() in (
        "1", "true", "yes", "on"
    )


# ── 公共 API 符号（预留，待后续步骤实现后填充实际导入） ──

# Hooks
from ._hooks import (
    use_state,
    use_effect,
    use_ref,
    use_memo,
    use_callback,
    use_context,
    use_reducer,
    create_context,
    get_hooks_runtime,
)

# Focus 系统
from ._focus import use_focus, use_focus_manager, FocusManager

__all__ = [
    # Feature Flag
    "_is_enabled",
    # Hooks
    "use_state",
    "use_effect",
    "use_ref",
    "use_memo",
    "use_callback",
    "use_context",
    "use_reducer",
    # Focus
    "use_focus",
    "use_focus_manager",
    "FocusManager",
    # Box
    "Box",
    "BoxBorderStyle",
    # Animation
    "use_animation",
    "AnimationClock",
    # Static
    "Static",
    # Transform
    "Transform",
    # Layout
    "FlexLayout",
    "FlexStyle",
    # DevTools
    "ErrorBoundary",
    "debug_component_tree",
    "inspect_hooks",
    # Message Blocks
    "ThinkingBlockBox",
    "AnswerBlockBox",
    "UserMsgBlockBox",
    "ToolOutputBlockBox",
    "ErrorBlockBox",
    "NotificationBlockBox",
    "TextContent",
    "create_message_box",
    # Types
    "HookState",
    "LayoutBox",
    "HookError",
    "LayoutError",
]
