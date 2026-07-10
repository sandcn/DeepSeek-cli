"""错误提示块 — ErrorBlock。

红色 ! 前缀，用于显示系统错误信息。
"""

from __future__ import annotations

from rich.text import Text

from ..const import _STYLE_ERROR, _MAX_ERROR_LENGTH
from ..utils import _truncate_msg
from ._base import TuiComponent


class ErrorBlock(TuiComponent):
    """错误提示块 — 红色 ! 前缀。"""
    def __init__(self, message: str):
        self.message = _truncate_msg(message, _MAX_ERROR_LENGTH)

    def render(self) -> Text:
        return Text.assemble(("\n  ! ", _STYLE_ERROR), (self.message, _STYLE_ERROR))
