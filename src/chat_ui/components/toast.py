"""Toast 组件 — React Ink 风格通知提示组件。

短暂出现的通知消息，支持 success/error/warn/info 预设。

使用示例:
    toast = Toast(message="保存成功", preset="success")
    print(toast.render())
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import TuiComponent
from ..infrastructure.styled import StyledText

if TYPE_CHECKING:
    from ..vdom.vnode import VNode


_PRESET_COLORS: dict[str, str] = {
    "success": "green",
    "error": "red",
    "warn": "yellow",
    "info": "blue",
}

_PRESET_ICONS: dict[str, str] = {
    "success": "\u2713",    # ✓
    "error": "\u2717",      # ✗
    "warn": "\u26a0",       # ⚠
    "info": "\u2139",       # ℹ
}


class Toast(TuiComponent):
    """React Ink Toast 组件 — 通知提示。

    单行通知信息，带图标和颜色。

    Props:
        message: str — 通知消息文本
        preset: str — 预设颜色 (success/error/warn/info)，无效值回退 "info"
        bold: bool — 是否加粗
        dim: bool — 是否暗色
        children: list[TuiComponent] — 子组件列表
    """

    def __init__(
        self,
        message: str = "",
        preset: str = "info",
        bold: bool = False,
        dim: bool = False,
        children: list[TuiComponent] | None = None,
    ):
        super().__init__(children=children)
        self._message = message
        self._preset = preset if preset in _PRESET_COLORS else "info"
        self._bold = bold
        self._dim = dim

    @property
    def key(self) -> str:
        return "toast"

    def update(self, props: dict) -> bool:
        changed = False
        if "message" in props and props["message"] != self._message:
            self._message = props["message"]
            changed = True
        if "preset" in props:
            new_p = props["preset"] if props["preset"] in _PRESET_COLORS else "info"
            if new_p != self._preset:
                self._preset = new_p
                changed = True
        if "bold" in props and props["bold"] != self._bold:
            self._bold = props["bold"]
            changed = True
        if "dim" in props and props["dim"] != self._dim:
            self._dim = props["dim"]
            changed = True
        return changed

    def render(self) -> str | StyledText:
        if not self._message:
            return ""

        color = _PRESET_COLORS.get(self._preset, "blue")
        icon = _PRESET_ICONS.get(self._preset, "\u2139")

        return StyledText(
            f" {icon} {self._message}",
            fg=color,
            bold=self._bold,
            dim=self._dim,
        )

    def render_vnode(self) -> VNode:
        from ..vdom.vnode import VNode
        rendered = self.render()
        return VNode(
            type="toast",
            key=self.key,
            props={
                "text": str(rendered) if rendered else "",
                "preset": self._preset,
            },
        )
