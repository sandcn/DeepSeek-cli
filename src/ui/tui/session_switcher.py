"""会话切换器 — 浏览和加载已保存的会话。
"""

from __future__ import annotations

import logging

from ...chat_msgs import list_sessions, load_session
from ..picker import Picker
from ._ttl_cache import TTLCache

_logger = logging.getLogger(__name__)


class SessionSwitcher:
    """会话切换器 — 列出已保存会话，选择后加载。

    用法：
        switcher = SessionSwitcher()
        data = switcher.show()
    """

    def __init__(self) -> None:
        # 会话列表缓存（60s TTL），避免每次 show() 都扫描文件系统
        self._cache = TTLCache(fetcher=list_sessions, ttl=60.0)

    def _get_cached_sessions(self) -> list[dict]:
        """获取缓存的会话列表（60s TTL）。

        避免每次 show() 都调用 list_sessions() 扫描文件系统。
        """
        return self._cache.get()

    def refresh_cache(self) -> None:
        """强制刷新会话缓存。"""
        self._cache.refresh()

    def show(self) -> dict[str, object] | None:
        """返回选中的会话信息 {'id': str, 'title': str, ...}，取消时返回 None。"""
        sessions = self._get_cached_sessions()
        if not sessions:
            return None

        items = []
        for s in sessions:
            title = s.get("title", "")
            title_info = f"「{title}」 " if title else ""
            sid_short = s['id'][:min(8, len(s['id']))]
            label = f"{sid_short}  {title_info}{s['model']}  {s['message_count']}msg  {s['saved_at']}"
            items.append(label)

        picker = Picker(title="Sessions", items=items, timeout=0)
        result = picker.run()

        if result.action == "confirmed" and result.selected_indices:
            idx = result.selected_indices[0]
            if idx < len(sessions):
                sid = sessions[idx].get("id", "")
                if not sid:
                    _logger.warning("SessionSwitcher.show: 选中会话缺少 id 字段，返回 None")
                    return None
                return load_session(sid)
        return None


__all__ = ["SessionSwitcher"]
