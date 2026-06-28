"""StatusDot 组件 — React Ink 风格状态指示点组件。

彩色圆点指示状态，支持 online/offline/away/busy/error。

使用示例:
    dot = StatusDot(status="online", label="服务运行中")
    print(dot.render())
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import TuiComponent
from ..infrastructure.styled import StyledText

if TYPE_CHECKING:
    from ..vdom.vnode import VNode


_STATUS_COLORS: dict[str, str] = {
    "online": "green",
    "offline": "white",
    "away": "yellow",
    "busy": "red",
    "error": "red",
}


class StatusDot(TuiComponent):
    """React Ink StatusDot 组件 — 状态指示点。

    渲染格式: ● label（彩色圆点 + 文本）

    Props:
        status: str — 状态 (online/offline/away/busy/error)，无效值回退 offline
        label: str — 标签文本（可选）
        size: str — 圆点大小 ("small" / "medium" / "large")，默认 "medium"
        children: list[TuiComponent] — 子组件列表

    圆点字符映射:
        small: • (U+2022)
        medium: ● (U+25CF)
        large: ⬟ (U+2B1F) — 使用 ◆ (U+25C6) 替代更兼容
    """

    _DOT_CHARS: dict[str, str] = {
        "small": "\u2022",
        "medium": "\u25CF",
        "large": "\u25C6",
    }

    def __init__(
        self,
        status: str = "offline",
        label: str = "",
        size: str = "medium",
        children: list[TuiComponent] | None = None,
    ):
        super().__init__(children=children)
        self._status = status if status in _STATUS_COLORS else "offline"
        self._label = label
        self._size = size if size in ("small", "medium", "large") else "medium"

    @property
    def key(self) -> str:
        return "status_dot"

    def update(self, props: dict) -> bool:
        changed = False
        if "status" in props:
            new_s = props["status"] if props["status"] in _STATUS_COLORS else "offline"
            if new_s != self._status:
                self._status = new_s
                changed = True
        if "label" in props and props["label"] != self._label:
            self._label = props["label"]
            changed = True
        if "size" in props:
            new_sz = props["size"] if props["size"] in ("small", "medium", "large") else "medium"
            if new_sz != self._size:
                self._size = new_sz
                changed = True
        return changed

    def render(self) -> str | StyledText:
        color = _STATUS_COLORS.get(self._status, "white")
        dot_char = self._DOT_CHARS.get(self._size, "\u25CF")

        if self._label:
            return StyledText(f"{dot_char} {self._label}", fg=color)
        return StyledText(dot_char, fg=color)

    def render_vnode(self) -> VNode:
        from ..vdom.vnode import VNode
        rendered = self.render()
        return VNode(
            type="status_dot",
            key=self.key,
            props={
                "text": str(rendered) if rendered else "",
                "status": self._status,
            },
        )
