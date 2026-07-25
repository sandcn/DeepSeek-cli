"""系统通知块 — NotificationBlock。

绿色 · 前缀，用于显示系统通知消息。

动效（2026-07-15 重构）：
  - 使用 Color256 / Style 替代 raw ANSI 拼接
  - 使用 StyleSheet 注册的语义色
  - FadeIn 入场：边框与辉光色从暗灰渐变至目标色（frame 0→6）
  - 保持窄屏降级行为不变
"""

from __future__ import annotations

from ._base import MessageBlock


class NotificationBlock(MessageBlock):
    """系统通知块 — 绿色 · 前缀。"""
    _icon = "·"
    _narrow_style_key = "neon"
    _glow_base = 47
    _glow_delta = 15
    _border_target = 23

    def __init__(self, text: str = "", *, props: dict | None = None) -> None:
        super().__init__(props=props)
        self.text = text

    def _get_message(self) -> str:
        return self.text
