"""命令面板 — 搜索和执行可用命令。

Ctrl+P 触发，在底部栏补全弹窗中显示命令列表。
"""

from __future__ import annotations

from ...core.commands import get_registered_command_names
from ._selector_base import BaseBottomBarSelector


class CommandPalette(BaseBottomBarSelector[str, str | None]):
    """命令面板 — 搜索并快速执行命令。

    继承 BaseBottomBarSelector，复用 TTLCache + run_bottom_bar_selection 通用流程。

    用法：
        palette = CommandPalette()
        result = palette.show()
    """

    def _fetch_items(self) -> list[str]:
        """获取所有注册的命令名列表（TTLCache 从 get_registered_command_names 获取）。"""
        return get_registered_command_names()

    def _on_selected(self, item: str) -> str | None:
        """用户选中命令后原样返回（带 "/" 前缀）。"""
        return item

    def _get_title(self) -> str:
        return "Command Palette"


__all__ = ["CommandPalette"]
