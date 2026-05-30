"""命令面板 — 搜索和执行可用命令。

Ctrl+P 触发，在底部栏补全弹窗中显示命令列表。
"""

from __future__ import annotations

from ...core.commands import get_registered_command_names
from .._bottom_bar import run_bottom_bar_selection
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
        """在底部栏补全弹窗中打开命令面板。

        Returns:
            用户选择的命令字符串，带 "/" 前缀（如 "/help"）；取消时返回 None。
        """
        commands = self._cache.get()
        if not commands:
            return None

        result = run_bottom_bar_selection(commands, commands, title="Command Palette")
        if result["action"] == "confirmed" and result["index"] is not None:
            return commands[result["index"]]
        return None


__all__ = ["CommandPalette"]
