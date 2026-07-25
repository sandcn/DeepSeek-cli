"""系统通知块 — NotificationBlock。

绿色 · 前缀，用于显示系统通知消息。
继承 StyledMessageBlock，通过参数化消除与 ErrorBlock 的重复。
"""

from __future__ import annotations

from ._base import StyledMessageBlock


class NotificationBlock(StyledMessageBlock):
    """系统通知块 — 绿色 · 前缀。"""
    def __init__(self, text: str = "", *, props: dict | None = None) -> None:
        super().__init__(
            prefix_char="·",
            color=47,
            narrow_style_key="neon",
            message=text,
            props=props,
            truncate=False,
        )
