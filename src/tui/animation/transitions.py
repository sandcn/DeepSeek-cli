"""过渡效果模块 — 提供界面元素过渡动画效果。

提供 FadeIn/FadeOut（渐显/渐隐）、SlideIn/SlideOut（滑入/滑出）、
Typewriter（打字机）等过渡效果。

所有过渡效果类均实现 ``render(frame: int) -> str`` 方法，
可与 ``composer.py`` 的合成器组合使用（满足 AnimationEffect Protocol）。

设计模式:
  - 策略 (Strategy): easing 参数选择不同的缓动策略
    （"smooth"/"bounce"/"linear" 三种策略可互换）
  - 模板方法 (Template Method): Typewriter 的逐帧 reveal 算法
    可作为骨架，子类可重载 ``_render_frame`` 实现不同展示策略
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..core.style import Style


__all__ = [
    "FadeIn", "FadeOut",
    "SlideIn", "SlideOut",
    "Typewriter",
]


# ═══════════════════════════════════════════════════════════
# 缓动策略 — 三种缓动函数
# ═══════════════════════════════════════════════════════════


def _easing_smooth(t: float) -> float:
    """正弦平滑缓动 [0,1] → [0,1]，两端减速。"""
    return (math.sin(t * math.pi - math.pi / 2.0) + 1.0) / 2.0


def _easing_bounce(t: float) -> float:
    """弹入缓动 [0,1] → [0,~1.1]，超调后稳定在 1.0。"""
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    return 1.0 - (1.0 - t) ** 2 + 0.12 * math.sin(t * math.pi * 5.0) * (1.0 - t)


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
# 视觉宽度工具（轻量版，避免导入 ui.ansi 全模块）
# ═══════════════════════════════════════════════════════════

try:
    from wcwidth import wcswidth as _wcswidth
    _HAS_WCWIDTH = True
except ImportError:
    _HAS_WCWIDTH = False


def _char_width(ch: str) -> int:
    """返回单个字符的终端视觉宽度（CJK=2，ASCII=1）。"""
    if _HAS_WCWIDTH:
        w = _wcswidth(ch)
        return w if w >= 0 else 1
    return 2 if ord(ch) > 127 else 1


def _text_visual_width(text: str) -> int:
    """计算字符串的终端视觉宽度（ANSI 安全）。"""
    # 简单版：不处理 ANSI 转义（过渡效果文本不含 ANSI 码）
    return sum(_char_width(ch) for ch in text)


def _slice_by_visual_width(text: str, target_vw: int, from_left: bool = True) -> str:
    """按视觉宽度截取字符串。

    Args:
        text: 原始字符串（不含 ANSI 码）。
        target_vw: 目标视觉宽度。
        from_left: True 从左侧开始截取，False 从右侧开始截取。

    Returns:
        视觉宽度不超过 target_vw 的子串。
    """
    if target_vw <= 0:
        return ""
    if from_left:
        result: list[str] = []
        vw = 0
        for ch in text:
            cw = _char_width(ch)
            if vw + cw > target_vw:
                break
            result.append(ch)
            vw += cw
        return "".join(result)
    else:
        # 从右侧截取：先找到不超过 target_vw 的起始位置
        total_vw = _text_visual_width(text)
        if total_vw <= target_vw:
            return text
        # 从末尾向前累加
        chars: list[str] = []
        vw = 0
        for ch in reversed(text):
            cw = _char_width(ch)
            if vw + cw > target_vw:
                break
            chars.append(ch)
            vw += cw
        return "".join(reversed(chars))


# ═══════════════════════════════════════════════════════════
# FadeIn / FadeOut — 渐显/渐隐过渡
# ═══════════════════════════════════════════════════════════


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
        if self._is_narrow():
            return ""
        if frame >= self.total_frames or self.total_frames <= 0:
            return ""
        t = frame / max(self.total_frames - 1, 1)
        easing_fn = _resolve_easing(self.easing)
        eased_t = easing_fn(t)
        color = round(self.start_color + eased_t * (self.end_color - self.start_color))
        color = max(0, min(255, color))
        return f"\033[38;5;{color}m"

    @staticmethod
    def _is_narrow() -> bool:
        """延迟导入窄屏检测。"""
        from ..terminal.narrow import is_narrow
        return is_narrow()


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
        if self._is_narrow():
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

    @staticmethod
    def _is_narrow() -> bool:
        from ..terminal.narrow import is_narrow
        return is_narrow()


# ═══════════════════════════════════════════════════════════
# SlideIn / SlideOut — 滑入/滑出过渡
# ═══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class SlideIn:
    """滑入过渡效果。

    在 total_frames 帧内逐步显示 text。支持左右方向滑入，
    窄屏时跳过过渡直接返回完整 text。

    设计模式: 策略 — direction 参数选择不同的滑入策略。

    Attributes:
        direction: 滑入方向，"left"（从左到右）/ "right"（从右到左）/ "top"（从上到下）。
        total_frames: 滑入总帧数。
        text: 要显示的文本内容。
    """
    direction: str = "left"
    total_frames: int = 6
    text: str = ""

    def render(self, frame: int) -> str:
        """返回帧 frame 对应的部分文本。

        Args:
            frame: 当前帧号（0-based）。

        Returns:
            frame=0 时返回空字符串，frame ≥ total_frames 时返回完整 text。
        """
        if not self.text or self.total_frames <= 0:
            return self.text
        # 窄屏跳过
        if self._is_narrow():
            return self.text
        if frame >= self.total_frames:
            return self.text
        if frame <= 0:
            return ""

        total_vw = _text_visual_width(self.text)
        target_vw = round(frame * total_vw / self.total_frames)
        target_vw = max(0, min(target_vw, total_vw))

        if self.direction == "right":
            # 右滑入（从右到左）：从左向右逐渐显示
            return _slice_by_visual_width(self.text, target_vw, from_left=True)
        else:
            # 左滑入（从左到右）/ 上滑入（从上到下）：从右向左逐渐显示
            return _slice_by_visual_width(self.text, target_vw, from_left=False)

    @staticmethod
    def _is_narrow() -> bool:
        from ..terminal.narrow import is_narrow
        return is_narrow()


@dataclass(frozen=True)
class SlideOut:
    """滑出过渡效果（SlideIn 的反向）。

    在 total_frames 帧内逐步隐藏 text。帧号推进时可见部分逐渐减少，
    窄屏时跳过过渡直接返回完整 text。

    Attributes:
        direction: 滑出方向，"left"（向左滑出）/ "right"（向右滑出）/ "top"（向上滑出）。
        total_frames: 滑出总帧数。
        text: 要显示的文本内容。
    """
    direction: str = "left"
    total_frames: int = 6
    text: str = ""

    def render(self, frame: int) -> str:
        """返回帧 frame 对应的部分文本。

        Args:
            frame: 当前帧号（0-based）。

        Returns:
            frame=0 时返回完整 text，frame ≥ total_frames 时返回空字符串。
        """
        if not self.text or self.total_frames <= 0:
            return ""
        if self._is_narrow():
            return self.text
        if frame >= self.total_frames:
            return ""
        if frame <= 0:
            return self.text

        total_vw = _text_visual_width(self.text)
        # 剩余可见比例：从 1.0 递减到 0.0
        remaining_ratio = 1.0 - frame / self.total_frames
        target_vw = round(remaining_ratio * total_vw)
        target_vw = max(0, min(target_vw, total_vw))

        if self.direction == "right":
            # 向右滑出：从右向左逐渐消失（保留左侧）
            return _slice_by_visual_width(self.text, target_vw, from_left=True)
        else:
            # 向左滑出 / 向上滑出：从左向右逐渐消失（保留右侧）
            return _slice_by_visual_width(self.text, target_vw, from_left=False)

    @staticmethod
    def _is_narrow() -> bool:
        from ..terminal.narrow import is_narrow
        return is_narrow()


# ═══════════════════════════════════════════════════════════
# Typewriter — 打字机过渡效果
# ═══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Typewriter:
    """打字机过渡效果。

    在 total_frames 帧内逐步揭示 text 字符。每帧显示
    ``chars_per_frame`` 个新字符，可选 ``style`` 对已显示文本
    应用样式。窄屏时跳过过渡直接返回完整 text。

    设计模式: 模板方法 — ``_render_frame`` 可被子类重载以
    实现不同的逐帧展示策略。

    Attributes:
        text: 要显示的文本内容。
        chars_per_frame: 每帧揭示的字符数，默认 1。
        total_frames: 总帧数，None 时自动计算为 ``ceil(len(text) / chars_per_frame)``。
        style: 可选的 Style 样式，用于对已揭示文本应用 ANSI 样式。
    """
    text: str = ""
    chars_per_frame: int = 1
    total_frames: int | None = None
    style: "Style | None" = None

    def __post_init__(self) -> None:
        """冻结 dataclass 的后期初始化：自动计算 total_frames。"""
        # frozen=True 时无法在 __init__ 后直接修改字段，
        # 使用 object.__setattr__ 绕过冻结限制来设置计算后的 total_frames
        if self.total_frames is None and self.text and self.chars_per_frame > 0:
            computed = math.ceil(len(self.text) / self.chars_per_frame)
            object.__setattr__(self, "total_frames", computed)
        elif self.total_frames is None:
            object.__setattr__(self, "total_frames", 0)

    def render(self, frame: int) -> str:
        """返回帧 frame 对应的部分文本。

        每帧揭示 chars_per_frame 个新字符，逐步构建完整文本。
        若设置了 style，对已揭示文本应用样式后返回。

        Args:
            frame: 当前帧号（0-based）。

        Returns:
            frame=0 时返回空字符串，frame ≥ total_frames 时返回完整 text。
        """
        if not self.text:
            return ""
        if self._is_narrow():
            return self.text

        total = self.total_frames or 0
        if total <= 0:
            return self.text
        if frame >= total:
            return self._apply_style(self.text)
        if frame <= 0:
            return ""

        reveal_count = min(frame * self.chars_per_frame, len(self.text))
        revealed = self.text[:reveal_count]
        return self._apply_style(revealed)

    def _apply_style(self, text: str) -> str:
        """对文本应用样式（若有）。"""
        if self.style is None:
            return text
        return self.style.apply(text)

    @staticmethod
    def _is_narrow() -> bool:
        from ..terminal.narrow import is_narrow
        return is_narrow()
