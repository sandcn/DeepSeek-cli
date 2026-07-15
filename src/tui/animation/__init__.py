"""动画基础设施层（Layer 0.5）。

提供动画合成和过渡效果。

本层提供:
  - AnimationEffect Protocol: 可组合的动画效果接口
  - CompositeEffect / EffectChain / InterleaveEffect: 动画合成器
  - anim_parallel / anim_sequence / anim_loop: 工厂函数
  - FadeIn / FadeOut / SlideIn / SlideOut / Typewriter: 过渡效果

.. note::
    AnimatorContext / BreathPalette 不再从此层重导出，
    请直接从 ``src.tui.core`` 导入：
    ``from src.tui.core import AnimatorContext, BreathPalette``
    （2026-07-15 步骤7精简：原重导出无外部调用方）
"""
from tui_framework.animation.composer import *
from tui_framework.animation.transitions import *

__all__ = [
    # composer
    "AnimationEffect", "CompositeEffect", "EffectChain", "InterleaveEffect",
    "anim_parallel", "anim_sequence", "anim_loop",
    # transitions
    "FadeIn", "FadeOut", "SlideIn", "SlideOut", "Typewriter",
]
