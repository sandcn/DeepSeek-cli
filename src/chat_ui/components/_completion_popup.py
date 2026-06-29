"""补全弹窗 — CompletionPopup。

浮动在输入行上方的候选项列表。
由底部栏 _CompletionPopup 负责实际渲染，此组件为数据模型。
"""

from __future__ import annotations

from ._base import TuiComponent


class CompletionPopup(TuiComponent):
    """补全弹窗 — 浮动在输入行上方的候选项列表。

    由底部栏 _CompletionPopup 负责实际渲染，此组件为数据模型。
    """
    def __init__(self):
        self.items: list[str] = []
        self.selected: int = 0
        self.visible: bool = False

    def show(self, items: list[str], selected: int = 0) -> None:
        self.items = items
        self.selected = selected
        self.visible = True

    def hide(self) -> None:
        self.visible = False
        self.items.clear()

    def render(self) -> str:
        if not self.visible:
            return ""
        lines = []
        for i, item in enumerate(self.items):
            prefix = "→ " if i == self.selected else "  "
            lines.append(f"{prefix}{item}")
        return "\n".join(lines)
