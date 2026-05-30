"""命令面板 — 搜索和执行可用命令。

Ctrl+P 触发，输入关键词实时过滤命令列表，回车执行选中命令。
"""

from __future__ import annotations

from ...core.commands import get_registered_command_names
from ..picker import Picker
from ._ttl_cache import TTLCache


class CommandPalette:
    """命令面板 — 搜索并快速执行命令。

    用法：
        palette = CommandPalette()
        result = palette.show()
    """

    def __init__(self) -> None:
        self._cache = TTLCache(fetcher=get_registered_command_names, ttl=60.0)

    def refresh(self) -> None:
        """刷新命令缓存（线程安全）。"""
        self._cache.refresh()

    def show(self) -> str | None:
        """打开命令面板，返回选中命令的完整字符串（如 '/help'）。

        Returns:
            用户选择的命令字符串，带 "/" 前缀（如 "/help"）；取消时返回 None。
        """
        commands = self._cache.get()
        if not commands:
            return None

        picker = Picker(title="Command Palette", items=commands, timeout=0)
        result = picker.run()

        if result.action == "confirmed" and result.selected_items:
            return result.selected_items[0]
        return None


__all__ = ["CommandPalette"]
