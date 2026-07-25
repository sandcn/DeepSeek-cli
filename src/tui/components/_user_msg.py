"""用户消息块 — UserMsgBlock。

"> text" 加粗样式，用于显示用户输入的消息。

动效（2026-07-12）：
  - 宽屏："> " 前缀使用 sparkle 闪烁，消息文本使用呼吸色
  - 窄屏：降级为静态 StyleSheet.get("user_icon")
"""

from __future__ import annotations

import re

from rich.text import Text

from ..animation.transitions import FadeIn
from ._base import TuiComponent
from ..core.style import StyleSheet
from ..core.theme import THEME
from ..animation.animator import AnimatorContext
from ..core.effects import build_fg_breath_ansi, sparkle_color
from ..render_buffer import RenderBuffer


class UserMsgBlock(TuiComponent):
    """用户消息块 — "> text" 加粗样式。"""
    def __init__(self, text: str = "", *, props: dict | None = None) -> None:
        super().__init__(props=props)
        self.text = text

    def _render_narrow(self) -> Text:
        """窄屏降级：静态样式消息文本，不加动效。"""
        user_style = StyleSheet.get("user_icon")
        user_rich_style = user_style.to_rich() if user_style else None
        return Text.assemble(
            ("\n  > ", user_rich_style),
            (self.text, user_rich_style),
        )

    def render(self, buffer: RenderBuffer | None = None) -> str | Text | None:
        result = self.render_with_narrow_fallback(buffer, narrow_method=self._render_narrow)
        if result is not None:
            return result

        # 宽屏："> " 前缀 sparkle 闪烁 + 消息文本呼吸色
        frame = AnimatorContext.get_default().frame

        # 从 THEME['user_glow'] 提取 sparkle base_color
        user_glow = THEME.get('user_glow', '\033[38;5;81m')
        glow_match = re.search(r"38;5;(\d+)", user_glow)
        sparkle_base = int(glow_match.group(1)) if glow_match else 81

        # 从 THEME['user'] 提取呼吸色 base_color
        user_color = THEME.get('user', '\033[38;5;45m')
        user_match = re.search(r"38;5;(\d+)", user_color)
        breath_base = int(user_match.group(1)) if user_match else 45

        sparkle_ansi = f"\033[38;5;{sparkle_color(frame, sparkle_base, period=6)}m"
        breath_ansi = build_fg_breath_ansi(frame, breath_base, min(255, breath_base + 20), 12)
        ansi_str = f"\n  {sparkle_ansi}>\033[0m {breath_ansi}{self.text}\033[0m"
        # 弹入动效：使用 FadeIn(bounce) 包裹，产生颜色弹入效果
        fade = FadeIn(easing="bounce", total_frames=6, start_color=240, end_color=255)
        fade_prefix = fade.render(frame)
        if fade_prefix:
            ansi_str = f"{fade_prefix}{ansi_str}\033[0m"
        result = Text.from_ansi(ansi_str)
        return self._finalize_render(result, buffer)
