"""React Ink — 声明式终端 UI 框架子包。

架构关系：本模块是 API 聚合层，实际实现分布在：
- vdom/hooks.py — Hooks 运行时（use_state/use_effect/use_ref/use_memo/use_callback）
- vdom/layout.py — FlexLayout 布局引擎
- vdom/types.py — HookState/HookError 类型
- components/animation.py — use_animation/AnimationClock
- components/spinner.py — use_spinner
- components/box.py — Box/边框/阴影
- components/message_blocks.py — 声明式消息块组件
- infrastructure/claude_style.py — Claude Code 风格门控
- devtools/stats.py — ErrorBoundary/调试工具
通过 CHAT_UI_USE_REACT_LIKE 环境变量门控启用。

提供 React Ink 风格的声明式终端 UI 能力。此包保留为兼容性 re-export，
实际模块已迁移至 vdom/ 和 components/ 子包。

所有新特性通过环境变量 CHAT_UI_USE_REACT_LIKE 门控，设为非空值启用。
"""

from __future__ import annotations

import os

# ── 子模块导入（从新位置 re-export） ──
from ..vdom.layout import FlexLayout, FlexStyle  # noqa: F401
from ..vdom.vnode import VNode, Patch, PatchKind, diff, apply_patches  # noqa: F401
from ..components.box import Box, BORDER_STYLES as BoxBorderStyle  # noqa: F401
from ..components.animation import use_animation, AnimationClock  # noqa: F401
from ..components.static import Static  # noqa: F401
from ..components.transform import Transform  # noqa: F401
from ..vdom.types import HookState, EffectState, HookError, LayoutBox, LayoutError  # noqa: F401
from ..vdom.builder import build_vnode_tree  # noqa: F401
from ..devtools.stats import ErrorBoundary, debug_component_tree, inspect_hooks, RenderStats  # noqa: F401

# Message Blocks（声明式 Box 包装组件）
from ..components.message_blocks import (  # noqa: F401
    ThinkingBlockBox,
    AnswerBlockBox,
    UserMsgBlockBox,
    ToolOutputBlockBox,
    ErrorBlockBox,
    NotificationBlockBox,
    TextContent,
    create_message_box,
)

from ..components.base import (  # noqa: F401
    TuiComponent,
    InputBarComponent,
    UserMsgBlock,
    ThinkingBlock,
    AnswerBlock,
    ToolOutputBlock,
    ToolSummaryBlock,
    ErrorBlock,
    NotificationBlock,
    WriteLineBlock,
    _estimate_content_lines,
)

from ..commands.types import CmdToolCallUpdate  # noqa: F401
from ..commands.types import (  # noqa: F401
    CmdReasoning,
    CmdContent,
    CmdPhaseDone,
    CmdToolOutput,
    CmdToolSummary,
    CmdUserMsg,
    CmdParseInfo,
    CmdNotification,
    CmdWriteLine,
    CmdDisplayMsgs,
    CmdToolCountInc,
    CmdToolFailInc,
    CmdError,
    CmdToolCountDec,
    CmdSubagentFrame,
    CmdInputChanged,
    CmdStatusUpdate,
    CmdAnimationTick,
    CmdSubagentSlotUpdate,
)
from ..commands.const import RenderCommand, _STYLE_DIM, _STYLE_BOLD, _STYLE_FAIL, _STYLE_WARN, _STYLE_SUCCESS, _STYLE_ERROR  # noqa: F401
from ..components.spinner import Spinner  # noqa: F401
from ..components.progress import Progress  # noqa: F401
from ..components.streaming_markdown import StreamingMarkdown  # noqa: F401
from ..components.tree import Tree, TreeNode  # noqa: F401
from ..components.subagent_tree import subagent_slots_to_tree  # noqa: F401
from ..components.animation import use_spinner, use_progress, use_typewriter, SPINNER_FRAMES, use_adaptive_animation, use_count_up, use_rainbow  # noqa: F401
from ..components.message_blocks import ToolCallBlockBox, ToolResultBlockBox  # noqa: F401

# Claude Code 风格模块
from ..infrastructure.claude_style import (  # noqa: F401
    _is_claude_style_enabled,
    _is_feature_enabled,
    CLAUDE_THINKING_ICON,
    CLAUDE_PROMPT_ICON,
    CLAUDE_SUCCESS_ICON,
    CLAUDE_FAIL_ICON,
    CLAUDE_TOOL_ICONS,
    CLAUDE_COLORS,
    CLAUDE_SPINNER_FRAMES,
    CLAUDE_TOOL_CARD_STYLE,
)

from ..infrastructure.styled import StyledText, Span  # noqa: F401
from ..infrastructure.protocol import BottomBarProtocol, RenderPhase, PanelContext  # noqa: F401


def _is_enabled() -> bool:
    """检测是否启用 React Ink 特性。

    优先通过 FeatureFlags 统一注册表读取，失败时回退到
    直接读取 CHAT_UI_USE_REACT_LIKE 环境变量。

    Returns:
        True 当 CHAT_UI_USE_REACT_LIKE 为启用的真值。
    """
    try:
        from src.shared_events.feature_flags import get_feature_flags
        return get_feature_flags().chat_ui_use_react_like
    except Exception:
        return os.environ.get("CHAT_UI_USE_REACT_LIKE", "").strip().lower() in (
            "1", "true", "yes", "on"
        )


# ── 公共 API 符号 ──

# Hooks
from ..vdom.hooks import (  # noqa: F401, E402
    _HooksRuntime,
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
from ..vdom.focus import use_focus, use_focus_manager, FocusManager  # noqa: F401, E402

__all__ = [
    # Feature Flag
    "_is_enabled",
    # Hooks
    "_HooksRuntime",
    "use_state",
    "use_effect",
    "use_ref",
    "use_memo",
    "use_callback",
    "use_context",
    "use_reducer",
    "create_context",
    "get_hooks_runtime",
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
    "use_spinner",
    "use_progress",
    "use_typewriter",
    "SPINNER_FRAMES",
    "use_adaptive_animation",
    "use_count_up",
    "use_rainbow",
    # Components
    "Spinner",
    "Progress",
    "StreamingMarkdown",
    "Tree",
    "TreeNode",
    "subagent_slots_to_tree",
    # Static
    "Static",
    # Transform
    "Transform",
    # Layout
    "FlexLayout",
    "FlexStyle",
    # VNode / Diff
    "VNode",
    "Patch",
    "PatchKind",
    "diff",
    "apply_patches",
    "build_vnode_tree",
    # Base Components
    "TuiComponent",
    "InputBarComponent",
    "UserMsgBlock",
    "ThinkingBlock",
    "AnswerBlock",
    "ToolOutputBlock",
    "ToolSummaryBlock",
    "ErrorBlock",
    "NotificationBlock",
    "WriteLineBlock",
    "_estimate_content_lines",
    # Styled
    "StyledText",
    "Span",
    # Protocol
    "BottomBarProtocol",
    "RenderPhase",
    "PanelContext",
    # DevTools
    "ErrorBoundary",
    "debug_component_tree",
    "inspect_hooks",
    "RenderStats",
    # Message Blocks
    "ThinkingBlockBox",
    "AnswerBlockBox",
    "UserMsgBlockBox",
    "ToolCallBlockBox",
    "ToolResultBlockBox",
    "ToolOutputBlockBox",
    "ErrorBlockBox",
    "NotificationBlockBox",
    "TextContent",
    "create_message_box",
    # Types
    "HookState",
    "EffectState",
    "LayoutBox",
    "HookError",
    "LayoutError",
    # Commands
    "CmdToolCallUpdate",
    "CmdReasoning",
    "CmdContent",
    "CmdPhaseDone",
    "CmdToolOutput",
    "CmdToolSummary",
    "CmdUserMsg",
    "CmdParseInfo",
    "CmdNotification",
    "CmdWriteLine",
    "CmdDisplayMsgs",
    "CmdToolCountInc",
    "CmdToolFailInc",
    "CmdError",
    "CmdToolCountDec",
    "CmdSubagentFrame",
    "CmdInputChanged",
    "CmdStatusUpdate",
    "CmdAnimationTick",
    "CmdSubagentSlotUpdate",
    # Render Commands
    "RenderCommand",
    "_STYLE_DIM",
    "_STYLE_BOLD",
    "_STYLE_FAIL",
    "_STYLE_WARN",
    "_STYLE_SUCCESS",
    "_STYLE_ERROR",
    # Claude Code Style
    "_is_claude_style_enabled",
    "_is_feature_enabled",
    "CLAUDE_THINKING_ICON",
    "CLAUDE_PROMPT_ICON",
    "CLAUDE_SUCCESS_ICON",
    "CLAUDE_FAIL_ICON",
    "CLAUDE_TOOL_ICONS",
    "CLAUDE_COLORS",
    "CLAUDE_SPINNER_FRAMES",
    "CLAUDE_TOOL_CARD_STYLE",
]
