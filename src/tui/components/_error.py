"""错误提示块 — ErrorBlock。

红色 ! 前缀，用于显示系统错误信息。

动效（2026-07-15 重构）：
  - 使用 Color256 / Style / BreathPalette 替代 raw ANSI 拼接
  - 使用 BreathPalette 脉动 + sine_color 辉光呼吸
  - FadeIn 入场：边框与辉光色从暗灰渐变至目标色（frame 0→6）
  - 保持窄屏降级行为不变
"""

from __future__ import annotations

import math

from rich.text import Text

from ..engine.const import _STYLE_ERROR_GRADIENT, _MAX_ERROR_LENGTH
from ..engine.utils import _truncate_msg
from ..animation.animator import BreathPalette
from ..core.style import Style, StyleSheet
from ..core.effects import sine_color
from ..framework import Framework
from ..terminal.terminal import is_narrow
from ._base import TuiComponent


# ── FadeIn 缓动因子 ─────────────────────────────────
_FADE_TOTAL_FRAMES = 6
_FADE_START_COLOR = 238  # 暗灰色起始


def _fade_factor(frame: int, total: int = _FADE_TOTAL_FRAMES) -> float:
    """计算 smooth easing 渐显因子 [0.0, 1.0]。

    FadeIn 不再通过 ANSI 前缀包裹（会被后续显式色号覆盖），
    而是将缓动因子融入边框/辉光色号的计算中——frame 0 时色号从 dim 开始，
    随帧递增渐亮至目标色。

    Args:
        frame: 当前帧号。
        total: 渐显总帧数。

    Returns:
        缓动因子，0.0（初始最暗）→ 1.0（全亮）。
    """
    if frame <= 0:
        return 0.0
    t = min(frame / total, 1.0)
    return (math.sin((t - 0.5) * math.pi) + 1) / 2


def _fade_color(target: int, fade: float,
                base: int = _FADE_START_COLOR) -> int:
    """将缓动因子融入色号：暗色 → 目标色。

    Args:
        target: 目标色号（256 色）。
        fade: 缓动因子 [0.0, 1.0]。
        base: 起始暗色。

    Returns:
        插值后的色号，clamp 到 [0, 255]。
    """
    return max(0, min(255, int(base + (target - base) * fade)))


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
        # 宽屏：! 前缀脉动 + 消息文本红色 glow 呼吸 + FadeIn 入场
        animator = Framework.get_default().get_animator()
        frame = animator.frame
        fade = _fade_factor(frame)
        # 脉动色号（BreathPalette），FadeIn 时从暗灰渐变
        pulse_raw = BreathPalette.get_color("error_pulse", animator.breath_frame)
        pulse_color = _fade_color(pulse_raw, fade)
        pulse_style = Style(fg=pulse_color)
        # 红色 glow 呼吸，FadeIn 期间色号范围从暗灰起步
        glow_lo = _fade_color(196, fade)
        glow_hi = _fade_color(min(255, 196 + 15), fade)
        glow_color = sine_color(frame, glow_lo, glow_hi, 12)
        glow_style = Style(fg=glow_color)
        # 左边缘呼吸边框，FadeIn 期间从暗灰色渐变至目标色
        border_breath = StyleSheet.resolve("border_breath", Style(fg=23))
        border_target = border_breath.fg if border_breath.fg is not None else 23
        border_lo = _fade_color(border_target, fade)
        border_hi = _fade_color(min(255, border_target + 2), fade)
        border_color = sine_color(frame, border_lo, border_hi, 24)
        border_style = Style(fg=border_color)
        ansi_str = (
            f"\n  {border_style.to_ansi()}\u2502\033[0m"
            f" {pulse_style.to_ansi()}! \033[0m"
            f"{glow_style.to_ansi()}{self.message}\033[0m"
        )
        return Text.from_ansi(ansi_str)
