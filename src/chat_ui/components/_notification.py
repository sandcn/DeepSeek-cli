"""系统通知块 — NotificationBlock。

绿色 · 前缀，用于显示系统通知消息。
"""

from __future__ import annotations

from rich.text import Text

from ..const import _STYLE_SUCCESS
from ._base import TuiComponent


class NotificationBlock(TuiComponent):
    """系统通知块 — 绿色 · 前缀。"""
    def __init__(self, text: str):
        self.text = text

    def render(self) -> Text:
        return Text.assemble(("\n  · ", _STYLE_SUCCESS), (self.text, _STYLE_SUCCESS))
