"""系统通知块 — NotificationBlock。

绿色 · 前缀，用于显示系统通知消息。

动效（2026-07-12）：
  - 宽屏："·" 前缀和消息文本使用绿色 glow 呼吸（深绿↔亮绿正弦呼吸）
  - 窄屏：降级为静态 _STYLE_NOTIFICATION_GRADIENT
"""

from __future__ import annotations

from rich.text import Text

from ..const import _STYLE_NOTIFICATION_GRADIENT
from ...ui.tui._animator import AnimatorContext
from ...ui.tui._terminal import is_narrow
from ...ui.tui._text_utils import build_glow_ansi
from ._base import TuiComponent


class NotificationBlock(TuiComponent):
    """系统通知块 — 绿色 · 前缀。"""
    def __init__(self, text: str):
        self.text = text

    def render(self) -> Text:
        if is_narrow():
            return Text.assemble(("\n  · ", _STYLE_NOTIFICATION_GRADIENT), (self.text, _STYLE_NOTIFICATION_GRADIENT))
        frame = AnimatorContext.get_default().frame
        glow_ansi = build_glow_ansi(frame, 47, 12)
        ansi_str = f"\n  {glow_ansi}· \033[0m{glow_ansi}{self.text}\033[0m"
        return Text.from_ansi(ansi_str)
