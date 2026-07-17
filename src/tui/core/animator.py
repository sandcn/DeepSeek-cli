"""向后兼容存根 — 从 animation/ 重导出。

原模块已迁移至 src/tui/animation/animator.py。
"""

from __future__ import annotations

from ..animation.animator import AnimatorContext, BreathPalette

__all__ = ["AnimatorContext", "BreathPalette"]
