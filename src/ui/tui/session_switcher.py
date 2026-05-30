"""会话切换器 — 浏览和加载已保存的会话。
"""

from __future__ import annotations

import logging

from ...chat_msgs import list_sessions, load_session
from ._selector_base import BaseBottomBarSelector

_logger = logging.getLogger(__name__)


class SessionSwitcher(BaseBottomBarSelector[dict, dict[str, object] | None]):
    """会话切换器 — 列出已保存会话，选择后加载。

    继承 BaseBottomBarSelector，复用 TTLCache + run_bottom_bar_selection 通用流程。

    用法：
        switcher = SessionSwitcher()
        data = switcher.show()

    向后兼容保留：
        refresh_cache() → 委托 self.refresh()
    """

    def _fetch_items(self) -> list[dict]:
        """扫描文件系统获取已保存会话列表（TTLCache 60s 缓存）。"""
        return list_sessions()

    def _format_display(self, items: list[dict]) -> list[str]:
        """格式化会话字典列表为显示标签。"""
        labels: list[str] = []
        for s in items:
            title = s.get("title", "")
            title_info = f"「{title}」 " if title else ""
            sid = s.get("id", "")
            sid_short = sid[:min(8, len(sid))] if sid else "?"
            label = f"{sid_short}  {title_info}{s.get('model', '?')}  {s.get('message_count', 0)}msg"
            labels.append(label)
        return labels

    def _on_selected(self, item: dict) -> dict[str, object] | None:
        """用户确认选择后调用 load_session 加载会话。"""
        sid = item.get("id", "")
        if not sid:
            _logger.warning("SessionSwitcher._on_selected: 会话缺少 id 字段")
            return None
        return load_session(sid)

    def _get_title(self) -> str:
        return "Sessions"

    # ── 向后兼容（保留旧方法名） ────────────────────────

    def refresh_cache(self) -> None:
        """强制刷新会话缓存（委托 refresh）。"""
        self.refresh()


__all__ = ["SessionSwitcher"]
