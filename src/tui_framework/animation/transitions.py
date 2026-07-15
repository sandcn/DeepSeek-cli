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
# 缓动策略
# ═══════════════════════════════════════════════════════════

from ..core.effects import sine_easing as _easing_smooth
from ..core.effects import bounce_easing as _easing_bounce


def _easing_linear(t: float) -> float:
    """线性缓动 [0,1] → [0,1]。"""
    return t


_EASING_MAP: dict[str, callable] = {
    "smooth": _easing_smooth,
    "bounce": _easing_bounce,
    "linear": _easing_linear,
}


def _resolve_easing(easing: str) -> callable:
    return _EASING_MAP.get(easing, _easing_smooth)


# ═══════════════════════════════════════════════════════════
# 视觉宽度工具
# ═══════════════════════════════════════════════════════════

try:
    from wcwidth import wcswidth as _wcswidth
    _HAS_WCWIDTH = True
except ImportError:
    _HAS_WCWIDTH = False


def _char_width(ch: str) -> int:
    if _HAS_WCWIDTH:
        w = _wcswidth(ch)
        return w if w >= 0 else 1
    return 2 if ord(ch) > 127 else 1


def _text_visual_width(text: str) -> int:
    return sum(_char_width(ch) for ch in text)


def _slice_by_visual_width(text: str, target_vw: int, from_left: bool = True) -> str:
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
        total_vw = _text_visual_width(text)
        if total_vw <= target_vw:
            return text
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
# FadeIn / FadeOut
# ═══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class FadeIn:
    """渐显过渡效果。"""

    easing: str = "smooth"
    total_frames: int = 6
    start_color: int = 238
    end_color: int = 255
    narrow: bool = False

    def render(self, frame: int) -> str:
        if self.narrow:
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
    """渐隐过渡效果。"""

    easing: str = "smooth"
    total_frames: int = 6
    start_color: int = 255
    end_color: int = 238
    narrow: bool = False

    def render(self, frame: int) -> str:
        if self.narrow:
            return ""
        if frame >= self.total_frames or self.total_frames <= 0:
            return ""
        t = frame / max(self.total_frames - 1, 1)
        easing_fn = _resolve_easing(self.easing)
        eased_t = easing_fn(t)
        color = round(self.end_color + (1.0 - eased_t) * (self.start_color - self.end_color))
        color = max(0, min(255, color))
        return f"\033[38;5;{color}m"


# ═══════════════════════════════════════════════════════════
# SlideIn / SlideOut
# ═══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class SlideIn:
    """滑入过渡效果。"""

    direction: str = "left"
    total_frames: int = 6
    text: str = ""
    narrow: bool = False

    def render(self, frame: int) -> str:
        if not self.text or self.total_frames <= 0:
            return self.text
        if self.narrow:
            return self.text
        if frame >= self.total_frames:
            return self.text
        if frame <= 0:
            return ""

        total_vw = _text_visual_width(self.text)
        target_vw = round(frame * total_vw / self.total_frames)
        target_vw = max(0, min(target_vw, total_vw))

        if self.direction == "right":
            return _slice_by_visual_width(self.text, target_vw, from_left=True)
        else:
            return _slice_by_visual_width(self.text, target_vw, from_left=False)


@dataclass(frozen=True)
class SlideOut:
    """滑出过渡效果。"""

    direction: str = "left"
    total_frames: int = 6
    text: str = ""
    narrow: bool = False

    def render(self, frame: int) -> str:
        if not self.text or self.total_frames <= 0:
            return ""
        if self.narrow:
            return self.text
        if frame >= self.total_frames:
            return ""
        if frame <= 0:
            return self.text

        total_vw = _text_visual_width(self.text)
        remaining_ratio = 1.0 - frame / self.total_frames
        target_vw = round(remaining_ratio * total_vw)
        target_vw = max(0, min(target_vw, total_vw))

        if self.direction == "right":
            return _slice_by_visual_width(self.text, target_vw, from_left=True)
        else:
            return _slice_by_visual_width(self.text, target_vw, from_left=False)


# ═══════════════════════════════════════════════════════════
# Typewriter
# ═══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Typewriter:
    """打字机过渡效果。"""

    text: str = ""
    chars_per_frame: int = 1
    total_frames: int | None = None
    style: "Style | None" = None
    narrow: bool = False

    def __post_init__(self) -> None:
        if self.total_frames is None and self.text and self.chars_per_frame > 0:
            computed = math.ceil(len(self.text) / self.chars_per_frame)
            object.__setattr__(self, "total_frames", computed)
        elif self.total_frames is None:
            object.__setattr__(self, "total_frames", 0)

    def render(self, frame: int) -> str:
        if not self.text:
            return ""
        if self.narrow:
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
        if self.style is None:
            return text
        return self.style.apply(text)
