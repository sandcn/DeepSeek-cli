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


def create_chat_ui():
    """创建 ChatUIConsumer（终端界面事件消费者）。"""
    from ...tui.consumer import ChatUIConsumer as _cls
    return _cls()


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


def get_plugin_view_state_cls():
    """返回 PluginViewState 数据类。"""
    from ...tui.app._state_types import PluginViewState as _cls
    return _cls


def get_model_view_state_cls():
    """返回 ModelViewState 数据类。"""
    from ...tui.app._state_types import ModelViewState as _cls
    return _cls


def _state_cls(name: str):
    """按名返回 ``_state_types`` 中的状态数据类（新增全屏视图批次共用）。"""
    from ...tui.app import _state_types as _mod
    return getattr(_mod, name)


def get_sessions_view_state_cls():
    """返回 SessionsViewState 数据类（会话浏览器）。"""
    return _state_cls("SessionsViewState")


def get_changes_view_state_cls():
    """返回 ChangesViewState 数据类（文件变更审查器）。"""
    return _state_cls("ChangesViewState")


def get_theme_view_state_cls():
    """返回 ThemeViewState 数据类（主题选择器）。"""
    return _state_cls("ThemeViewState")


def get_skill_view_state_cls():
    """返回 SkillViewState 数据类（技能浏览器）。"""
    return _state_cls("SkillViewState")


def get_mcp_view_state_cls():
    """返回 McpViewState 数据类（MCP 服务器管理）。"""
    return _state_cls("McpViewState")


def get_usage_view_state_cls():
    """返回 UsageViewState 数据类（用量仪表盘）。"""
    return _state_cls("UsageViewState")


def get_search_view_state_cls():
    """返回 SearchViewState 数据类（对话内全文搜索）。"""
    return _state_cls("SearchViewState")


def get_keymap_view_state_cls():
    """返回 KeymapViewState 数据类（键位自定义编辑器）。"""
    return _state_cls("KeymapViewState")


def get_notify_view_state_cls():
    """返回 NotifyViewState 数据类（通知 / 事件日志）。"""
    return _state_cls("NotifyViewState")


def get_export_view_state_cls():
    """返回 ExportViewState 数据类（导出向导）。"""
    return _state_cls("ExportViewState")


def get_logs_view_state_cls():
    """返回 LogsViewState 数据类（会话日志 / 投影浏览器）。"""
    return _state_cls("LogsViewState")


def list_keybindings() -> list:
    """返回当前生效的键位绑定（供键位编辑器展示；core 层不直连 tui）。"""
    try:
        from ...tui._keybindings import (
            active_keybindings,
            default_keybinding,
            key_to_combo,
        )
    except Exception:
        return []
    out: list = []
    for spec_id, spec in active_keybindings().items():
        try:
            default_combo = key_to_combo(default_keybinding(spec_id).key)
        except Exception:
            default_combo = ""
        out.append({
            "id": spec_id,
            "key": spec.key,
            "combo": key_to_combo(spec.key),
            "default_combo": default_combo,
            "action": spec.action,
            "description": spec.description,
        })
    return out


def register_keybinding_override(spec_id: str, combo: str) -> bool:
    """注册一个键位覆盖（运行时生效；不持久化）。

    组合键文本非法 / 未知绑定 id → 返回 False。
    """
    try:
        from ...tui._keybindings import (
            KeyBinding,
            combo_to_key,
            default_keybinding,
            register_builtin_keybinding,
        )
    except Exception:
        return False
    key = combo_to_key(combo)
    if key is None:
        return False
    try:
        base = default_keybinding(spec_id)
    except Exception:
        return False
    try:
        register_builtin_keybinding(
            spec_id, KeyBinding(spec_id, key, base.action, base.description),
        )
    except Exception:
        return False
    return True


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
    "create_chat_ui",
    "create_display_proxy",
    "emit_display_event",
    "get_subagent_panel_controller",
    "get_user_select_state_cls",
    "get_config_view_state_cls",
    "get_plugin_view_state_cls",
    "get_model_view_state_cls",
    "get_sessions_view_state_cls",
    "get_changes_view_state_cls",
    "get_theme_view_state_cls",
    "get_skill_view_state_cls",
    "get_mcp_view_state_cls",
    "get_usage_view_state_cls",
    "get_search_view_state_cls",
    "get_keymap_view_state_cls",
    "get_notify_view_state_cls",
    "get_export_view_state_cls",
    "get_logs_view_state_cls",
    "list_keybindings",
    "register_keybinding_override",
    "get_theme_registry",
    "invalidate_palette_cache",
    "render_diff_to_ansi",
    "display_messages",
    "edit_current_messages",
    "get_message_editor_cls",
    "restore_feedback",
]
