"""错误提示块 — ErrorBlock。

红色 ! 前缀，用于显示系统错误信息。
继承 StyledMessageBlock，通过参数化消除与 NotificationBlock 的重复。
"""

from __future__ import annotations

from ..framework import get_framework
from ._base import StyledMessageBlock


class ErrorBlock(StyledMessageBlock):
    """错误提示块 — 红色 ! 前缀。"""
    def __init__(self, message: str = "", *, props: dict | None = None) -> None:
        _max_len = get_framework().get_config().max_error_length
        super().__init__(
            prefix_char="!",
            color=196,
            narrow_style_key="error",
            message=message,
            props=props,
            truncate=True,
            max_len=_max_len,
        )
