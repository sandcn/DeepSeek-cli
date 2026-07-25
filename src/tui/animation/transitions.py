"""过渡效果模块 — 提供界面元素过渡动画效果。

提供 FadeIn/FadeOut（渐显/渐隐）等过渡效果。

所有过渡效果类均实现 ``render(frame: int) -> str`` 方法，
可与 ``composer.py`` 的合成器组合使用（满足 AnimationEffect Protocol）。

设计模式:
  - 策略 (Strategy): easing 参数选择不同的缓动策略
    （"smooth"/"bounce"/"linear" 三种策略可互换）
"""

from __future__ import annotations

from dataclasses import dataclass


__all__ = [
    "FadeIn", "FadeOut",
]


# ═══════════════════════════════════════════════════════════
# 缓动策略 — 三种缓动函数
# ═══════════════════════════════════════════════════════════


# sine_easing 已在 src.tui.core.effects 中定义，此处引用统一入口
from ..core.effects import sine_easing as _easing_smooth


# bounce_easing 已在 src.tui.core.effects 中定义，此处引用统一入口
from ..core.effects import bounce_easing as _easing_bounce

# 窄屏检测 — 统一入口（替代各类的 _is_narrow() 静态方法）
from ..terminal.terminal import is_narrow


def _easing_linear(t: float) -> float:
    """线性缓动 [0,1] → [0,1]。"""
    return t


# ── 缓动策略调度表 ─────────────────────────────────────
_EASING_MAP: dict[str, callable] = {
    "smooth": _easing_smooth,
    "bounce": _easing_bounce,
    "linear": _easing_linear,
}


def _resolve_easing(easing: str) -> callable:
    """按名称解析缓动函数，未知名称回退 smooth。"""
    return _EASING_MAP.get(easing, _easing_smooth)


# ═══════════════════════════════════════════════════════════
# FadeIn / FadeOut — 渐显/渐隐过渡
# ═══════════════════════════════════════════════════════════
#
# 【已清理】_is_narrow() 重复定义已消除 — 统一使用模块级导入
# ``from ..terminal.terminal import is_narrow``，替代各类的 @staticmethod。


@dataclass(frozen=True)
class FadeIn:
    """渐显过渡效果。

    在 total_frames 帧内从 start_color（暗）渐变到 end_color（亮），
    返回 ANSI 前景色转义序列。帧号超出 total_frames 时返回空字符串。

    设计模式: 策略 — easing 参数选择不同的缓动策略。

    Attributes:
        easing: 缓动类型，"smooth"（正弦平滑）/ "bounce"（弹跳）/ "linear"（线性）。
        total_frames: 渐显总帧数。
        start_color: 起始色号（暗色），默认 238（暗灰）。
        end_color: 结束色号（亮色），默认 255（最亮白）。
    """
    easing: str = "smooth"
    total_frames: int = 6
    start_color: int = 238
    end_color: int = 255

    def render(self, frame: int) -> str:
        """返回帧 frame 对应的 ANSI 前景色序列。

        Args:
            frame: 当前帧号（0-based）。

        Returns:
            ANSI 前景色序列 ``\\033[38;5;{color}m``，
            frame ≥ total_frames 时返回空字符串。
        """
        # 窄屏跳过
        if is_narrow():
            return ""
        if frame >= self.total_frames or self.total_frames <= 0:
            return ""
        t = frame / max(self.total_frames - 1, 1)
        easing_fn = _resolve_easing(self.easing)
        eased_t = easing_fn(t)
        color = round(self.start_color + eased_t * (self.end_color - self.start_color))
        color = max(0, min(255, color))
        return f"\033[38;5;{color}m"


@dataclass(frozen=True)
class FadeOut:
    """渐隐过渡效果（FadeIn 的反向）。

    在 total_frames 帧内从 start_color（亮）渐变到 end_color（暗），
    返回 ANSI 前景色转义序列。帧号超出 total_frames 时返回空字符串。

    Attributes:
        easing: 缓动类型，"smooth"（正弦平滑）/ "bounce"（弹跳）/ "linear"（线性）。
        total_frames: 渐隐总帧数。
        start_color: 起始色号（亮色），默认 255（最亮白）。
        end_color: 结束色号（暗色），默认 238（暗灰）。
    """
    easing: str = "smooth"
    total_frames: int = 6
    start_color: int = 255
    end_color: int = 238

    def render(self, frame: int) -> str:
        """返回帧 frame 对应的 ANSI 前景色序列。

        Args:
            frame: 当前帧号（0-based）。

        Returns:
            ANSI 前景色序列，frame ≥ total_frames 时返回空字符串。
        """
        if is_narrow():
            return ""
        if frame >= self.total_frames or self.total_frames <= 0:
            return ""
        # FadeOut 与 FadeIn 逻辑对称：渐亮 → 渐暗
        t = frame / max(self.total_frames - 1, 1)
        easing_fn = _resolve_easing(self.easing)
        eased_t = easing_fn(t)
        # 从亮到暗：反向插值
        color = round(self.end_color + (1.0 - eased_t) * (self.start_color - self.end_color))
        color = max(0, min(255, color))
        return f"\033[38;5;{color}m"


