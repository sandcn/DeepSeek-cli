"""动画基础设施层（Layer 0.5）。

提供：
  - AnimatorContext: 全局单例动画时钟管理器
  - BreathPalette:   呼吸颜色注册表
"""

from __future__ import annotations

from .animator import AnimatorContext
from .breath import BreathPalette

__all__ = [
    "AnimatorContext",
    "BreathPalette",
]
