"""系统通知块 — NotificationBlock。

绿色 · 前缀，用于显示系统通知消息。

动效（2026-07-15 重构）：
  - 使用 Color256 / Style 替代 raw ANSI 拼接
  - 使用 StyleSheet 注册的语义色
  - FadeIn 入场：边框与辉光色从暗灰渐变至目标色（frame 0→6）
  - 保持窄屏降级行为不变
"""

from __future__ import annotations

import math

from rich.text import Text

from ..engine.const import _STYLE_NOTIFICATION_GRADIENT, _STYLE_DIM
from ..core.style import Style, StyleSheet
from ..terminal.terminal import is_narrow
from ..core.effects import sine_color
from ..framework import Framework
from ._base import TuiComponent


# ── FadeIn 缓动因子（与 _error.py 共享算法） ──────
_FADE_TOTAL_FRAMES = 6
_FADE_START_COLOR = 238


def _fade_factor(frame: int, total: int = _FADE_TOTAL_FRAMES) -> float:
    """计算 smooth easing 渐显因子 [0.0, 1.0]。

    将缓动因子融入边框/辉光色号的计算中——frame 0 时色号从 dim 开始，
    随帧递增渐亮至目标色。避免 ANSI 前缀被后续显式色号覆盖导致无可见效果。
    """
    if frame <= 0:
        return 0.0
    t = min(frame / total, 1.0)
    return (math.sin((t - 0.5) * math.pi) + 1) / 2


def _fade_color(target: int, fade: float,
                base: int = _FADE_START_COLOR) -> int:
    """将缓动因子融入色号：暗色 → 目标色。"""
    return max(0, min(255, int(base + (target - base) * fade)))


class NotificationBlock(TuiComponent):
    """系统通知块 — 绿色 · 前缀。"""
    def __init__(self, text: str):
        self.text = text

    def render(self) -> Text:
        if is_narrow():
            return Text.assemble(("\n  · ", _STYLE_NOTIFICATION_GRADIENT), (self.text, _STYLE_NOTIFICATION_GRADIENT))
        animator = Framework.get_default().get_animator()
        frame = animator.frame
        fade = _fade_factor(frame)
        # 绿色辉光呼吸，FadeIn 期间从暗灰渐亮
        glow_lo = _fade_color(47, fade)
        glow_hi = _fade_color(min(255, 47 + 15), fade)
        glow_color = sine_color(frame, glow_lo, glow_hi, 12)
        glow_style = Style(fg=glow_color)
        # 左边缘呼吸边框，FadeIn 期间从暗灰渐变至目标色
        border_breath = StyleSheet.resolve("border_breath", Style(fg=23))
        border_target = border_breath.fg if border_breath.fg is not None else 23
        border_lo = _fade_color(border_target, fade)
        border_hi = _fade_color(min(255, border_target + 2), fade)
        border_color = sine_color(frame, border_lo, border_hi, 24)
        border_style = Style(fg=border_color)
        ansi_str = (
            f"\n  {border_style.to_ansi()}\u2502\033[0m"
            f" {glow_style.to_ansi()}· \033[0m"
            f"{glow_style.to_ansi()}{self.text}\033[0m"
        )
        return Text.from_ansi(ansi_str)
