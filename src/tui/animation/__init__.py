"""动画基础设施层（Layer 0.5）。

提供动画合成和过渡效果。AnimatorContext / BreathPalette 从 core 层重导出。

核心组件（core 层）:
  - AnimatorContext: 全局单例动画时钟管理器（从 ..core.animator 重导出）
  - BreathPalette:   呼吸颜色注册表（从 ..core.animator 重导出）

本层提供:
  - AnimationEffect Protocol: 可组合的动画效果接口
  - CompositeEffect / EffectChain / InterleaveEffect: 动画合成器
  - anim_parallel / anim_sequence / anim_loop: 工厂函数
  - FadeIn / FadeOut / SlideIn / SlideOut / Typewriter: 过渡效果
"""

from __future__ import annotations

from ..core.animator import AnimatorContext, BreathPalette

# ── 动画合成器 ──
from .composer import (
    AnimationEffect, CompositeEffect, EffectChain, InterleaveEffect,
    anim_parallel, anim_sequence, anim_loop,
)

# ── 过渡效果 ──
from .transitions import FadeIn, FadeOut, SlideIn, SlideOut, Typewriter

__all__ = [
    "AnimatorContext",
    "BreathPalette",
    # composer
    "AnimationEffect", "CompositeEffect", "EffectChain", "InterleaveEffect",
    "anim_parallel", "anim_sequence", "anim_loop",
    # transitions
    "FadeIn", "FadeOut", "SlideIn", "SlideOut", "Typewriter",
]
