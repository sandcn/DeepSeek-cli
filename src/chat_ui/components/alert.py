"""Alert 组件 — React Ink 风格警示框组件。

提供 <Alert preset="success" title="成功" message="操作成功"> 组件。

preset 预设：
  - "success" — 绿色 ✓ 图标
  - "error" — 红色 ✗ 图标  
  - "warn" — 黄色 ⚠ 图标
  - "info" — 蓝色 ℹ 图标

使用示例:
    alert = Alert(preset="success", title="成功", message="操作已完成")
    print(alert.render())
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


class Alert(TuiComponent):
    """React Ink Alert 组件 — 多预设警示框。

    带图标前缀和颜色样式的警示消息，支持关闭按钮。

    Props:
        preset: str — 预设颜色方案 (success/error/warn/info)，无效值回退到 "info"
        title: str — 警示标题（可选）
        message: str — 警示详细消息（可选）
        closable: bool — 是否显示关闭标记 [×]
        bold: bool — 是否加粗标题
        children: list[TuiComponent] — 子组件列表
    """

    def __init__(
        self,
        preset: str = "info",
        title: str = "",
        message: str = "",
        closable: bool = False,
        bold: bool = True,
        children: list[TuiComponent] | None = None,
    ):
        super().__init__(children=children)
        self._preset = preset if preset in _PRESET_COLORS else "info"
        self._title = title
        self._message = message
        self._closable = closable
        self._bold = bold

    @property
    def key(self) -> str:
        return "alert"

    def update(self, props: dict) -> bool:
        changed = False
        if "preset" in props:
            new_preset = props["preset"] if props["preset"] in _PRESET_COLORS else "info"
            if new_preset != self._preset:
                self._preset = new_preset
                changed = True
        if "title" in props and props["title"] != self._title:
            self._title = props["title"]
            changed = True
        if "message" in props and props["message"] != self._message:
            self._message = props["message"]
            changed = True
        if "closable" in props and props["closable"] != self._closable:
            self._closable = props["closable"]
            changed = True
        if "bold" in props and props["bold"] != self._bold:
            self._bold = props["bold"]
            changed = True
        return changed

    def render(self) -> str | StyledText:
        color = _PRESET_COLORS.get(self._preset, "blue")
        icon = _PRESET_ICONS.get(self._preset, "\u2139")

        parts: list[str | StyledText] = []

        # 标题行: [图标] 标题 [×]
        title_parts: list[str | StyledText] = []
        title_parts.append(StyledText(f" {icon} ", fg=color, bold=True))
        if self._title:
            title_parts.append(StyledText(self._title, fg=color, bold=self._bold))
        else:
            title_parts.append(StyledText(self._preset.upper(), fg=color, bold=self._bold))
        if self._closable:
            title_parts.append(StyledText(" [\u00d7]", fg=color, dim=True))

        alert_title = StyledText.assemble(*title_parts)
        parts.append(alert_title)

        # 消息行
        if self._message:
            parts.append(StyledText(f"  {self._message}", fg=color, dim=True))

        # 子组件
        children_output = self.render_children()
        if children_output:
            if isinstance(children_output, (str, StyledText)):
                parts.append(StyledText(f"  {children_output}" if isinstance(children_output, str) else children_output))

        if len(parts) == 1:
            return parts[0]
        return StyledText.assemble(*parts)

    def render_vnode(self) -> VNode:
        from ..vdom.vnode import VNode
        rendered = self.render()
        return VNode(
            type="alert",
            key=self.key,
            props={
                "text": str(rendered) if rendered else "",
                "preset": self._preset,
            },
        )
