"""Badge 组件 — React Ink 风格徽章组件。

提供 <Badge text="..." preset="success"> 组件，用于显示带颜色的状态标签。

preset 预设：
  - "success" — 绿色前景
  - "error" — 红色前景
  - "warn" — 黄色前景
  - "info" — 蓝色前景
  - "default" — dim 暗色，无前景/背景色

使用示例:
    badge = Badge(text="成功", preset="success")
    print(badge.render())

    badge = Badge(text="失败", preset="error", bold=True)
    print(badge.render())
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import TuiComponent
from ..infrastructure.styled import StyledText

if TYPE_CHECKING:
    from ..vdom.vnode import VNode


# ── 预设颜色映射 ─────────────────────────────────────────
_PRESET_COLORS: dict[str, str | None] = {
    "success": "green",
    "error": "red",
    "warn": "yellow",
    "info": "blue",
    "default": None,
}


class Badge(TuiComponent):
    """React Ink Badge 组件 — 状态徽章。

    用于显示带颜色的状态标签文字。

    Props:
        text: str — 徽章文本内容。
        preset: str — 预设颜色方案。
            "success" → 绿色文字
            "error" → 红色文字
            "warn" → 黄色文字
            "info" → 蓝色文字
            "default" → dim 暗色，无前景色
        bold: bool — 是否加粗。
        italic: bool — 是否斜体。
        dim: bool — 是否暗色（非 default preset 时叠加）。
        children: list[TuiComponent] — 子组件列表。
    """

    def __init__(
        self,
        text: str = "",
        preset: str = "default",
        bold: bool = False,
        italic: bool = False,
        dim: bool = False,
        children: list[TuiComponent] | None = None,
    ):
        """初始化 Badge 组件。

        Args:
            text: 徽章文本，空字符串时 render() 返回 ""。
            preset: 预设颜色方案，无效值回退到 "default"。
            bold: 是否加粗文本。
            italic: 是否斜体。
            dim: 是否暗色（default preset 时始终 dim=True，忽略此参数）。
            children: 子组件列表。
        """
        super().__init__(children=children)
        self._text = text
        self._preset = self._normalize_preset(preset)
        self._bold = bold
        self._italic = italic
        self._dim = dim

    @staticmethod
    def _normalize_preset(preset: str) -> str:
        """标准化预设名称，无效时回退到 default。"""
        if preset in _PRESET_COLORS:
            return preset
        return "default"

    @property
    def key(self) -> str:
        """稳定标识符 — 用于 VNode Diff 的 key 匹配。"""
        return "badge"

    def update(self, props: dict) -> bool:
        """接收新 props，对比变化决定是否重渲染。

        Args:
            props: 新的属性字典。

        Returns:
            True 如果任何属性发生变化。
        """
        changed = False
        if "text" in props and props["text"] != self._text:
            self._text = props["text"]
            changed = True
        if "preset" in props:
            new_preset = self._normalize_preset(props["preset"])
            if new_preset != self._preset:
                self._preset = new_preset
                changed = True
        if "bold" in props and props["bold"] != self._bold:
            self._bold = props["bold"]
            changed = True
        if "italic" in props and props["italic"] != self._italic:
            self._italic = props["italic"]
            changed = True
        if "dim" in props and props["dim"] != self._dim:
            self._dim = props["dim"]
            changed = True
        return changed

    def render(self) -> str | StyledText:
        """渲染徽章。

        - text 为空时返回空字符串 ""
        - default preset：使用 dim=True，无 fg/bg
        - 其他 preset：使用对应 fg 颜色
        - bold/italic/dim 属性叠加于 preset 之上
        """
        if not self._text:
            return ""

        color = _PRESET_COLORS.get(self._preset)
        if self._preset == "default":
            # default: dim=True，无 fg/bg
            return StyledText(
                self._text,
                bold=self._bold,
                italic=self._italic,
                dim=True,
            )
        else:
            # 其他 preset: 使用对应 fg 颜色
            return StyledText(
                self._text,
                fg=color,
                bold=self._bold,
                italic=self._italic,
                dim=self._dim,
            )

    def render_vnode(self) -> VNode:
        """产出 VNode — 声明式渲染的主入口。

        Returns:
            VNode(type="badge", key="badge", props=...)
        """
        from ..vdom.vnode import VNode
        rendered = self.render()
        return VNode(
            type="badge",
            key=self.key,
            props={
                "text": str(rendered) if rendered else "",
                "preset": self._preset,
            },
        )
