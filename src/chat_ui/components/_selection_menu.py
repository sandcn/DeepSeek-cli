"""底部选择菜单 — SelectionMenu。

供 user_select / 消息编辑 / 命令面板等使用。
由底部栏 _BottomBar.run_bottom_bar_selection() 实际渲染。
"""

from __future__ import annotations

from ._base import TuiComponent


class SelectionMenu(TuiComponent):
    """底部选择菜单 — 供 user_select / 消息编辑 / 命令面板等使用。

    由底部栏 _BottomBar.run_bottom_bar_selection() 实际渲染。
    """
    def __init__(self):
        self.items: list[str] = []
        self.selected: int = 0
        self.visible: bool = False
        self.title: str = ""

    def render(self) -> str:
        if not self.visible:
            return ""
        lines = [f"  {self.title}"] if self.title else []
        for i, item in enumerate(self.items):
            prefix = "▶ " if i == self.selected else "  "
            lines.append(f"{prefix}{item}")
        return "\n".join(lines)
