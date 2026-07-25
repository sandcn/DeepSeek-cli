"""错误提示块 — ErrorBlock。

红色 ! 前缀，用于显示系统错误信息。

动效（2026-07-15 重构）：
  - 使用 Color256 / Style / BreathPalette 替代 raw ANSI 拼接
  - 使用 BreathPalette 脉动 + sine_color 辉光呼吸
  - FadeIn 入场：边框与辉光色从暗灰渐变至目标色（frame 0→6）
  - 保持窄屏降级行为不变
"""

from __future__ import annotations

from ..engine.const import _truncate_msg
from ..framework import get_framework
from ._base import MessageBlock


class ErrorBlock(MessageBlock):
    """错误提示块 — 红色 ! 前缀。"""
    _icon = "!"
    _narrow_style_key = "error"
    _glow_base = 196
    _glow_delta = 15
    _border_target = 23

    def __init__(self, message: str = "", *, props: dict | None = None) -> None:
        super().__init__(props=props)
        _max_len = get_framework().get_config().max_error_length
        self.message = _truncate_msg(message, _max_len)

    def _get_message(self) -> str:
        return self.message
