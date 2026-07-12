"""错误提示块 — ErrorBlock。

红色 ! 前缀（使用呼吸色增强），用于显示系统错误信息。
"""

from __future__ import annotations

from rich.text import Text

from ..const import _STYLE_ERROR_GRADIENT, _MAX_ERROR_LENGTH
from ..utils import _truncate_msg
from ..renderer.bridge import get_sparkle_color
from ._base import TuiComponent
from rich.style import Style


class ErrorBlock(TuiComponent):
    """错误提示块 — 红色 ! 前缀，带呼吸/闪烁增强。"""
    def __init__(self, message: str):
        self.message = _truncate_msg(message, _MAX_ERROR_LENGTH)

    def render(self) -> Text:
        # 使用闪烁色增强错误图标
        try:
            sparkle = get_sparkle_color(196, 6)  # 红色闪烁
            pulse_style = Style(color=sparkle, bold=True)
        except Exception:
            pulse_style = _STYLE_ERROR_GRADIENT
        return Text.assemble(
            ("\n  ! ", pulse_style),
            (self.message, _STYLE_ERROR_GRADIENT),
        )
