"""用户消息块 — UserMsgBlock。

"> text" 加粗样式（使用呼吸色增强），用于显示用户输入的消息。
"""

from __future__ import annotations

from rich.text import Text
from rich.style import Style

from ._base import TuiComponent
from ..const import _STYLE_USER_GRADIENT
from ..renderer.bridge import get_breath_color


class UserMsgBlock(TuiComponent):
    """用户消息块 — "> text" 加粗样式，`>` 前缀使用呼吸色增强。"""
    def __init__(self, text: str):
        self.text = text

    def render(self) -> Text:
        try:
            breath = get_breath_color(45, 81, 12)  # 青色呼吸
            prompt_style = Style(color=breath, bold=True)
        except Exception:
            prompt_style = _STYLE_USER_GRADIENT
        return Text.assemble(
            ("\n  > ", prompt_style),
            (self.text, _STYLE_USER_GRADIENT),
        )
