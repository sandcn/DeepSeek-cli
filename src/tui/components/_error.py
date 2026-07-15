"""错误提示块 — ErrorBlock。

红色 ! 前缀，用于显示系统错误信息。

动效（2026-07-15 重构）：
  - 使用 Color256 / Style / BreathPalette 替代 raw ANSI 拼接
  - 使用 BreathPalette 脉动 + sine_color 辉光呼吸
  - 保持窄屏降级行为不变
"""

from __future__ import annotations

from rich.text import Text

from ..consumer.const import _STYLE_ERROR_GRADIENT, _MAX_ERROR_LENGTH
from ..consumer.utils import _truncate_msg
from ..core.animator import AnimatorContext, BreathPalette
from ..core.style import Style, StyleSheet
from ..core.color import Color256
from ..core.effects import sine_color
from ..terminal.terminal import is_narrow
from ._base import TuiComponent


class ErrorBlock(TuiComponent):
    """错误提示块 — 红色 ! 前缀。"""
    def __init__(self, message: str):
        self.message = _truncate_msg(message, _MAX_ERROR_LENGTH)

    def render(self) -> Text:
        if is_narrow():
            return Text.assemble(
                ("\n  ! ", _STYLE_ERROR_GRADIENT),
                (self.message, _STYLE_ERROR_GRADIENT),
            )
        # 宽屏：! 前缀脉动 + 消息文本红色 glow 呼吸
        frame = AnimatorContext.get_default().frame
        # 使用 BreathPalette 构建脉动色（替代 build_warning_pulse_ansi）
        pulse_color = BreathPalette.get_color("error_pulse", AnimatorContext.get_default().breath_frame)
        pulse_style = Style(fg=pulse_color)
        # 红色 glow 呼吸（替代 build_glow_ansi）
        glow_color = sine_color(frame, 196, min(255, 196 + 15), 12)
        glow_style = Style(fg=glow_color)
        # 左边缘呼吸边框（替代 build_left_border_ansi）
        border_breath = StyleSheet.resolve("border_breath", Style(fg=23))
        border_color = sine_color(frame, border_breath.fg if border_breath.fg is not None else 23,
                                   min(255, (border_breath.fg if border_breath.fg is not None else 23) + 2), 24)
        border_style = Style(fg=border_color)
        ansi_str = (
            f"\n  {border_style.to_ansi()}\u2502\033[0m"
            f" {pulse_style.to_ansi()}! \033[0m"
            f"{glow_style.to_ansi()}{self.message}\033[0m"
        )
        return Text.from_ansi(ansi_str)
