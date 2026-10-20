"""AppViews — 视图状态所有权（P0-2 / P2-1 架构修复）。

背景：修复前 ``AppModel`` 直接持有全部视图状态（trace 的 40+ 字段、
fullscreen/bottom_view、14 个视图 State 对象），组件通过
``model.trace_selected`` / ``model.fullscreen`` 直接读写——单一可变对象
成为所有视图的隐式通信总线，视图生命周期没有 owner。

本模块把视图状态从 ``AppModel`` 迁出：

  - ``NavigationState``：当前全屏/底部视图（导航栈）；
  - ``TraceViewState``：轨迹视图全部浏览/搜索/导出状态；
  - 其余视图 State（config/plugin/model/logs/sandbox/...）：由 ``AppViews``
    统一持有；
  - ``ViewStateStore`` 语义收敛在本类：``open_fullscreen`` / ``close_fullscreen``
    / ``open_bottom`` / ``close_bottom`` / ``reset_view``——打开/关闭/重置
    有唯一入口，视图控制器不再各自散落清理。

``AppModel`` 保留**兼容访问器**（``__getattr__``/``__setattr__`` 转发到
``AppViews``），使既有组件在迁移期零改动；字段的真实归属已经转移，
``AppModel.__dict__`` 不再持有这些视图字段。新代码应直接使用
``model.views.<view>``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ._state_types import (
    ChangesViewState,
    ConfigViewState,
    ExportViewState,
    KeymapViewState,
    LogsViewState,
    McpViewState,
    ModelViewState,
    NotifyViewState,
    PluginViewState,
    SandboxHistoryViewState,
    SandboxRecordsViewState,
    SandboxStatsViewState,
    SearchViewState,
    SessionsViewState,
    SkillViewState,
    ThemeViewState,
    UsageViewState,
)


@dataclass
class NavigationState:
    """导航状态：当前模态全屏视图 / 底部视图（空串=正常界面）。"""

    fullscreen: str = ""
    bottom_view: str = ""


@dataclass
class TraceViewState:
    """轨迹视图状态（从 AppModel 迁出的 trace_* 字段）。"""

    trace_selected: int = -1
    trace_pane: str = "ledger"
    trace_inspector_scroll: int = 0
    trace_inspector_cursor: int = 0
    trace_subagent_label: Any = None
    trace_tools_selected: int = 0
    trace_tools_pane: str = "ledger"
    trace_tools_scroll: int = 0
    trace_tools_cursor: int = 0
    trace_tree_collapsed: set = field(default_factory=set)
    trace_tools_tree_collapsed: set = field(default_factory=set)
    trace_search_mode: bool = False
    trace_search_query: str = ""
    trace_search_pattern: str = ""
    trace_search_side: str = ""
    trace_search_matches: list = field(default_factory=list)
    trace_search_idx: int = -1
    trace_count_buffer: str = ""
    trace_pending_prefix: str = ""
    trace_help_open: bool = False
    trace_stats_open: bool = False
    trace_status_message: str = ""
    trace_search_case: bool = False
    trace_search_filter: bool = False
    trace_marks: dict = field(default_factory=dict)
    trace_search_history: list = field(default_factory=list)
    trace_search_hist_idx: int = -1
    trace_kind_filter: str = ""
    trace_time_mode: str = "off"
    trace_show_line_numbers: bool = False
    trace_raw_text: bool = False
    trace_expanded: set = field(default_factory=set)
    trace_show_time_bar: bool = True
    trace_collapsed_turns: set = field(default_factory=set)
    trace_compare: list = field(default_factory=list)
    trace_export_scope: str = "all"


#: 视图 id → AppViews 属性名（视图对象本身）
VIEW_OBJECTS = (
    "config_view",
    "plugin_view",
    "model_view",
    "sessions_view",
    "changes_view",
    "sandbox_view",
    "sandbox_history_view",
    "sandbox_records_view",
    "theme_view",
    "skill_view",
    "mcp_view",
    "usage_view",
    "search_view",
    "keymap_view",
    "notify_view",
    "export_view",
    "logs_view",
)


class AppViews:
    """全部视图状态容器 + 视图生命周期入口。"""

    def __init__(self) -> None:
        self.navigation = NavigationState()
        self.trace = TraceViewState()
        self.config_view = ConfigViewState()
        self.plugin_view = PluginViewState()
        self.model_view = ModelViewState()
        self.sessions_view = SessionsViewState()
        self.changes_view = ChangesViewState()
        self.sandbox_view = SandboxStatsViewState()
        self.sandbox_history_view = SandboxHistoryViewState()
        self.sandbox_records_view = SandboxRecordsViewState()
        self.theme_view = ThemeViewState()
        self.skill_view = SkillViewState()
        self.mcp_view = McpViewState()
        self.usage_view = UsageViewState()
        self.search_view = SearchViewState()
        self.keymap_view = KeymapViewState()
        self.notify_view = NotifyViewState()
        self.export_view = ExportViewState()
        self.logs_view = LogsViewState()

    # ── 字段归属映射（兼容访问器） ─────────────────────

    @classmethod
    def field_owners(cls) -> dict[str, tuple[str, str | None]]:
        """字段名 → (AppViews 属性, 子字段或 None)。"""
        owners: dict[str, tuple[str, str | None]] = {
            "fullscreen": ("navigation", "fullscreen"),
            "bottom_view": ("navigation", "bottom_view"),
        }
        for name in TraceViewState.__dataclass_fields__:
            owners[name] = ("trace", name)
        for name in VIEW_OBJECTS:
            owners[name] = (name, None)
        return owners

    def get_field(self, name: str, default: Any = None) -> Any:
        owner = self.field_owners().get(name)
        if owner is None:
            return default
        obj_name, sub = owner
        obj = getattr(self, obj_name)
        return obj if sub is None else getattr(obj, sub, default)

    def set_field(self, name: str, value: Any) -> bool:
        owner = self.field_owners().get(name)
        if owner is None:
            return False
        obj_name, sub = owner
        if sub is None:
            setattr(self, obj_name, value)
        else:
            setattr(getattr(self, obj_name), sub, value)
        return True

    # ── 视图生命周期（P2-1） ──────────────────────────

    def open_fullscreen(self, view_id: str) -> None:
        self.navigation.fullscreen = str(view_id or "")

    def close_fullscreen(self) -> None:
        self.navigation.fullscreen = ""

    def open_bottom(self, view_id: str) -> None:
        self.navigation.bottom_view = str(view_id or "")

    def close_bottom(self) -> None:
        self.navigation.bottom_view = ""

    def reset_view(self, view_id: str) -> bool:
        """重置视图状态（关闭时清理；未知视图返回 False）。"""
        if view_id == "trace":
            self.trace = TraceViewState()
            return True
        if view_id in VIEW_OBJECTS:
            current = getattr(self, view_id)
            setattr(self, view_id, type(current)())
            return True
        return False

    def reset_all(self) -> None:
        self.navigation = NavigationState()
        self.trace = TraceViewState()
        for name in VIEW_OBJECTS:
            current = getattr(self, name)
            setattr(self, name, type(current)())


__all__ = [
    "AppViews",
    "NavigationState",
    "TraceViewState",
    "VIEW_OBJECTS",
]
