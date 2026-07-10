"""UI 端口适配器 — 默认实现，延迟导入 ui/ 包

所有适配器使用延迟导入，避免在 core 层模块加载时触发 ui 包的导入。
"""

from __future__ import annotations

import logging
from typing import Any

from ..ports.ui import (
    ThemePort,
    BottomBarPort,
    DiffRendererPort,
    MsgEditPort,
    ParallelDisplayPort,
)

_logger = logging.getLogger(__name__)


class DefaultThemeAdapter(ThemePort):
    """默认主题适配器 — 委托给 src.ui.theme"""

    def set_theme(self, name: str) -> None:
        from ...ui.theme import set_theme
        set_theme(name)

    def get_active_theme(self) -> str:
        from ...ui.theme import get_active_theme
        return get_active_theme()

    def get_theme_names_with_desc(self) -> list[tuple[str, str]]:
        from ...ui.theme import get_theme_names_with_desc
        return get_theme_names_with_desc()


class DefaultBottomBarAdapter(BottomBarPort):
    """默认底部栏适配器 — 委托给 src.ui._bottom_bar"""

    def run_bottom_bar_selection(
        self,
        items: list[Any],
        display_items: list[str] | None = None,
        title: str = "",
        bottom_bar: Any = None,
        active_theme: str | None = None,
    ) -> Any | None:
        from ...ui._bottom_bar import run_bottom_bar_selection
        return run_bottom_bar_selection(items, display_items, title, bottom_bar, active_theme)


class DefaultDiffRendererAdapter(DiffRendererPort):
    """默认差异渲染适配器 — 委托给 src.ui.diff_renderer"""

    def render_diff_to_ansi(self, file_path: str, before: str, after: str) -> str:
        from ...ui.diff_renderer import render_diff_to_ansi
        return render_diff_to_ansi(file_path, before, after)


class DefaultMsgEditAdapter(MsgEditPort):
    """默认消息编辑适配器 — 委托给 src.ui.msg_list"""

    def edit_current_messages(
        self,
        messages: list[dict],
        system_messages: list[dict],
        agent_name: str | None = None,
    ) -> list[dict] | None:
        from ...ui.msg_list import edit_current_messages
        return edit_current_messages(messages, system_messages, agent_name)


class DefaultParallelDisplayAdapter(ParallelDisplayPort):
    """默认并行显示适配器 — 委托给 src.ui.parallel"""

    def create_parallel_session(
        self,
        session_id: str,
        agent_type: str,
        callback: Any = None,
    ) -> Any:
        from ...ui.parallel import ParallelDisplay
        return ParallelDisplay(session_id, agent_type, callback)

    def get_active_chat_ui(self) -> Any:
        try:
            from ...chat_ui.state import get_active_chat_ui
            return get_active_chat_ui()
        except Exception:
            _logger.debug("get_active_chat_ui 不可用")
            return None

    def get_agent_type_abbrev(self, agent_type: str) -> str:
        from ...ui.parallel._tool_icons import AGENT_TYPE_ABBREV
        return AGENT_TYPE_ABBREV.get(agent_type, agent_type[:3].upper())
