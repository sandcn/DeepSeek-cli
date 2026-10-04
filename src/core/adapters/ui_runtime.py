"""UI 运行时桥接工厂 — core 领域/命令层访问表现层的唯一入口

职责：将核心层对表现层（tui/renderer）的运行时依赖集中到本适配器模块，
使 core 领域层与 core.commands 不直接 import tui/renderer，维持单向依赖：

    表现层 (tui/renderer) ──依赖──▶ core.ports（抽象）
    表现层 (tui/renderer) ◀──桥接── core.adapters（本模块）

所有函数体内延迟导入 tui/renderer，避免模块加载副作用与循环依赖；
导入路径与原调用点保持一致，保证测试 patch 路径不失效。
"""

from __future__ import annotations

from typing import Any, Optional


def get_active_chat_ui():
    """返回当前活跃 ChatUIConsumer（无活跃时 None）。"""
    from ...tui.consumer import get_active_chat_ui as _fn
    return _fn()


def create_display_proxy(source: str = "", max_history: int = 3):
    """创建 BaseDisplay 接口代理（事件驱动显示）。"""
    from ...tui.events import EventBusDisplayProxy as _cls
    return _cls(source=source, max_history=max_history)


def emit_display_event(event: Any) -> None:
    """发布显示事件到默认 DisplayEventBus。"""
    from ...tui.events.publish import emit as _fn
    _fn(event)


def get_subagent_panel_controller():
    """返回 SubAgent 面板控制器类。"""
    from ...tui.subagent import SubAgentPanelController as _cls
    return _cls


def get_user_select_state_cls():
    """返回 UserSelectState 数据类。"""
    from ...tui.app.model import UserSelectState as _cls
    return _cls


def get_config_view_state_cls():
    """返回 ConfigViewState 数据类。"""
    from ...tui.app._state_types import ConfigViewState as _cls
    return _cls


def get_theme_registry():
    """返回 ThemeRegistry（主题集单一真源）。"""
    from ...tui.core._theme import ThemeRegistry as _cls
    return _cls


def invalidate_palette_cache() -> None:
    """失效调色板缓存（主题切换后生效）。"""
    from ...tui.core._theme import _invalidate_palette_cache as _fn
    _fn()


def render_diff_to_ansi(path: str, old_content: str, new_content: str) -> str:
    """将文件差异渲染为带 ANSI 颜色的字符串。"""
    from ...tui._diff_renderer import render_diff_to_ansi as _fn
    return _fn(path, old_content, new_content)


def display_messages(
    data: list,
    agent: Any = None,
    idx_map: Optional[list] = None,
    speed: int = 0,
) -> None:
    """非对话 UI 上下文下直写显示消息（兜底路径）。"""
    from ...tui.pipeline.message_display import display_messages as _fn
    _fn(data, agent=agent, idx_map=idx_map, speed=speed)


def edit_current_messages(
    agent: Any,
    state: dict,
    bottom_bar: Any = None,
    input_: Any = None,
) -> bool:
    """编辑当前消息列表。"""
    from ...tui.pipeline.message_editor import edit_current_messages as _fn
    return _fn(agent, state, bottom_bar=bottom_bar, input_=input_)


def get_message_editor_cls():
    """返回 MessageEditor 类。"""
    from ...tui.pipeline.message_editor import MessageEditor as _cls
    return _cls


def restore_feedback(*args: Any, **kwargs: Any):
    """恢复消息编辑器反馈（pipeline.message_editor._restore_feedback）。"""
    from ...tui.pipeline.message_editor import _restore_feedback as _fn
    return _fn(*args, **kwargs)


def get_message_editor_helpers():
    """返回消息编辑器纯函数三元组（_content_has_nontext/_text_part_str/_truncate_messages）。"""
    from ...tui.pipeline.message_editor import (
        _content_has_nontext as _a,
        _text_part_str as _b,
        _truncate_messages as _c,
    )
    return _a, _b, _c


__all__ = [
    "get_active_chat_ui",
    "create_display_proxy",
    "emit_display_event",
    "get_subagent_panel_controller",
    "get_user_select_state_cls",
    "get_config_view_state_cls",
    "get_theme_registry",
    "invalidate_palette_cache",
    "render_diff_to_ansi",
    "display_messages",
    "edit_current_messages",
    "get_message_editor_cls",
    "restore_feedback",
]
