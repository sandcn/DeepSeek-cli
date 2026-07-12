"""系统通知块 — NotificationBlock。

绿色 · 前缀（使用呼吸色增强），用于显示系统通知消息。
"""

from __future__ import annotations

from rich.text import Text
from rich.style import Style

from ..const import _STYLE_NOTIFICATION_GRADIENT
from ..renderer.bridge import get_breath_color
from ._base import TuiComponent


class NotificationBlock(TuiComponent):
    """系统通知块 — 绿色 · 前缀，带呼吸色增强。"""
    def __init__(self, text: str):
        self.text = text

    def render(self) -> Text:
        try:
            breath = get_breath_color(41, 47, 12)  # 绿色呼吸
            breath_style = Style(color=breath, bold=True)
        except Exception:
            breath_style = _STYLE_NOTIFICATION_GRADIENT
        return Text.assemble(
            ("\n  · ", breath_style),
            (self.text, _STYLE_NOTIFICATION_GRADIENT),
        )
