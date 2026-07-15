"""系统通知块 — NotificationBlock。

绿色 · 前缀，用于显示系统通知消息。

动效（2026-07-15 重构）：
  - 使用 Color256 / Style 替代 raw ANSI 拼接
  - 使用 StyleSheet 注册的语义色
  - 保持窄屏降级行为不变
"""

from __future__ import annotations

from rich.text import Text

from ..consumer.const import _STYLE_NOTIFICATION_GRADIENT, _STYLE_DIM
from ..core.animator import AnimatorContext
from ..core.style import Style, StyleSheet
from ..core.color import Color256
from ..terminal.terminal import is_narrow
from ..core.effects import sine_color
from ._base import TuiComponent


class NotificationBlock(TuiComponent):
    """系统通知块 — 绿色 · 前缀。"""
    def __init__(self, text: str):
        self.text = text

    def render(self) -> Text:
        if is_narrow():
            return Text.assemble(("\n  · ", _STYLE_NOTIFICATION_GRADIENT), (self.text, _STYLE_NOTIFICATION_GRADIENT))
        frame = AnimatorContext.get_default().frame
        # 使用 Color256 + sine_color 构建辉光呼吸（替代 build_glow_ansi）
        glow_color = sine_color(frame, 47, min(255, 47 + 15), 12)
        glow_style = Style(fg=glow_color)
        # 左边缘呼吸边框（替代 build_left_border_ansi）
        border_breath = StyleSheet.resolve("border_breath", Style(fg=23))
        border_color = sine_color(frame, border_breath.fg if border_breath.fg is not None else 23,
                                   min(255, (border_breath.fg if border_breath.fg is not None else 23) + 2), 24)
        border_style = Style(fg=border_color)
        ansi_str = (
            f"\n  {border_style.to_ansi()}\u2502\033[0m"
            f" {glow_style.to_ansi()}· \033[0m"
            f"{glow_style.to_ansi()}{self.text}\033[0m"
        )
        return Text.from_ansi(ansi_str)
