"""Meter 组件 — React Ink 风格仪表/进度指示组件。

显示水平进度条 + 百分比，支持颜色区间（低/中/高）。

使用示例:
    meter = Meter(value=75, label="内存")
    print(meter.render())
"""

from __future__ import annotations

import builtins
import math
from typing import TYPE_CHECKING

from .base import TuiComponent
from ..infrastructure.styled import StyledText

if TYPE_CHECKING:
    from ..vdom.vnode import VNode


# 颜色区间阈值
_LOW_THRESHOLD = 33
_MED_THRESHOLD = 66


class Meter(TuiComponent):
    """React Ink Meter 组件 — 仪表/进度条。

    颜色区间：
    - 低 (0-33%): red
    - 中 (34-66%): yellow
    - 高 (67-100%): green

    Props:
        value: float — 当前值
        min: float — 最小值，默认 0
        max: float — 最大值，默认 100
        label: str — 标签文本（可选，show_label=True 时显示）
        show_label: bool — 是否显示标签，默认 False
        bar_width: int — 进度条字符宽度，默认 20
        show_percent: bool — 是否显示百分比数字，默认 True
        children: list[TuiComponent] — 子组件列表
    """

    def __init__(
        self,
        value: float = 0,
        min: float = 0,
        max: float = 100,
        label: str = "",
        show_label: bool = False,
        bar_width: int = 20,
        show_percent: bool = True,
        children: list[TuiComponent] | None = None,
    ):
        super().__init__(children=children)
        self._value = value
        self._min = min
        self._max = max
        self._label = label
        self._show_label = show_label
        self._bar_width = builtins.max(4, bar_width)
        self._show_percent = show_percent

    @property
    def key(self) -> str:
        return "meter"

    def _clamp(self, v: float) -> float:
        """将值限制在 [min, max] 范围内。"""
        return builtins.max(self._min, builtins.min(self._max, v))

    def _percent(self) -> float:
        """计算百分比 [0, 100]。"""
        diff = self._max - self._min
        if diff <= 0:
            return 0.0
        return ((self._clamp(self._value) - self._min) / diff) * 100.0

    def _bar_color(self, pct: float) -> str:
        """根据百分比返回颜色名。"""
        if pct <= _LOW_THRESHOLD:
            return "red"
        elif pct <= _MED_THRESHOLD:
            return "yellow"
        return "green"

    def update(self, props: dict) -> bool:
        changed = False
        for key in ("value", "min", "max", "label", "show_label", "bar_width", "show_percent"):
            if key in props and props[key] != getattr(self, f"_{key}", None):
                if key == "bar_width":
                    setattr(self, f"_{key}", builtins.max(4, props[key]))
                else:
                    setattr(self, f"_{key}", props[key])
                changed = True
        return changed

    def render(self) -> str | StyledText:
        pct = self._percent()
        color = self._bar_color(pct)
        bar_w = self._bar_width
        filled = builtins.min(bar_w, builtins.max(0, int(round(pct / 100.0 * bar_w))))
        empty = bar_w - filled

        bar = "\u2588" * filled + "\u2591" * empty  # █ + ░

        parts: list[str | StyledText] = []

        # 标签
        if self._show_label and self._label:
            parts.append(StyledText(f"{self._label} ", dim=True))

        # 进度条
        parts.append(StyledText(bar, fg=color))

        # 百分比
        if self._show_percent:
            parts.append(StyledText(f" {int(round(pct))}%", dim=True))

        children_output = self.render_children()
        if children_output:
            parts.append(StyledText(" "))
            if isinstance(children_output, str):
                parts.append(StyledText(children_output))
            else:
                parts.append(children_output)

        if not parts:
            return ""

        return StyledText.assemble(*parts) if len(parts) > 1 else parts[0]

    def render_vnode(self) -> VNode:
        from ..vdom.vnode import VNode
        rendered = self.render()
        pct = self._percent()
        return VNode(
            type="meter",
            key=self.key,
            props={
                "text": str(rendered) if rendered else "",
                "percent": pct,
                "color": self._bar_color(pct),
            },
        )
