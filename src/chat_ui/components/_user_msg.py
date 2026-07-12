"""用户消息块 — UserMsgBlock。

"> text" 加粗样式，用于显示用户输入的消息。
"""

from __future__ import annotations

from rich.text import Text

from ._base import TuiComponent
from ..const import _STYLE_USER_GRADIENT


class UserMsgBlock(TuiComponent):
    """用户消息块 — "> text" 加粗样式。"""
    def __init__(self, text: str):
        self.text = text

    def render(self) -> Text:
        return Text.assemble(("\n  > ", _STYLE_USER_GRADIENT), (self.text, _STYLE_USER_GRADIENT))
