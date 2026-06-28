"""chat_ui 数据模型模块 — StatusLine / InputLine / CompletionPopup / SelectionMenu"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StatusLine:
    """状态行 — 模型名 · tokens · 时间 · 工具计数。

    由底部栏 _BottomBar 负责实际渲染，此组件为数据模型。
    """
    model: str = ""
    tokens: int = 0
    elapsed: float = 0.0
    tool_count: int = 0
    tool_fail: int = 0
    streaming: bool = False
    round_start_time: float = 0.0

    def render(self) -> str:
        """渲染为单行状态文本。"""
        parts = []
        if self.model:
            parts.append(self.model)
        if self.tokens:
            parts.append(f"{self.tokens}t")
        if self.elapsed:
            parts.append(f"{self.elapsed:.1f}s")
        if self.tool_count:
            s = f"⚙{self.tool_count}"
            if self.tool_fail:
                s += f"!{self.tool_fail}"
            parts.append(s)
        return " · ".join(parts) if parts else ""


@dataclass
class InputLine:
    """输入行 — > 提示符 + 用户输入文本 + 光标。

    由底部栏 _BottomBar 负责实际渲染，此组件为数据模型。
    """
    text: str = ""
    cursor_pos: int = 0

    def render(self) -> str:
        return f"> {self.text}"


@dataclass
class CompletionPopup:
    """补全弹窗 — 浮动在输入行上方的候选项列表。

    由底部栏 _CompletionPopup 负责实际渲染，此组件为数据模型。
    """
    items: list[str] = field(default_factory=list)
    selected: int = 0
    visible: bool = False

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


@dataclass
class SelectionMenu:
    """底部选择菜单 — 供 user_select / 消息编辑 / 命令面板等使用。

    由底部栏 _BottomBar.run_bottom_bar_selection() 实际渲染。
    """
    items: list[str] = field(default_factory=list)
    selected: int = 0
    visible: bool = False
    title: str = ""

    def render(self) -> str:
        if not self.visible:
            return ""
        lines = [f"  {self.title}"] if self.title else []
        for i, item in enumerate(self.items):
            prefix = "▶ " if i == self.selected else "  "
            lines.append(f"{prefix}{item}")
        return "\n".join(lines)
