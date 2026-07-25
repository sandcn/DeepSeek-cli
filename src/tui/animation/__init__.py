"""动画基础设施层（Layer 0.5）。

提供动画过渡效果。

本层提供:
  - FadeIn / FadeOut: 过渡效果

.. note::
    AnimatorContext / BreathPalette 不再从此层重导出，
    请直接从 ``src.tui.core`` 导入：
    ``from src.tui.core import AnimatorContext, BreathPalette``
    （2026-07-15 步骤7精简：原重导出无外部调用方）
"""

from __future__ import annotations

# ── 过渡效果 ──
from .transitions import FadeIn, FadeOut

__all__ = [
    "FadeIn", "FadeOut",
]
