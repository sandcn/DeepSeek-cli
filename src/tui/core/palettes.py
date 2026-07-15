"""
预定义渐变调色板与呼吸调色板常量。

从 :mod:`src.ui.colors` 提取，依赖 :mod:`src.tui.core.gradient` 中的
``gradient_range`` 函数在模块加载时计算色号列表。

所有调色板常量均为 ``list[int]`` 类型，可直接用于 :class:`GradientDescriptor`
和动画效果。
"""
from tui_framework.core.palettes import *

__all__: list[str] = [
    # ── 核心调色板 ──
    "GRADIENT_SUNSET",
    "GRADIENT_OCEAN",
    "GRADIENT_FOREST",
    "GRADIENT_FIRE",
    "GRADIENT_NEON",
    "GRADIENT_AURORA",
    "GRADIENT_CORAL",
    "GRADIENT_MINT",
    "GRADIENT_TWILIGHT",
    # ── 第四阶段调色板 ──
    "GRADIENT_SUNRISE",
    "GRADIENT_PURPLE",
    "GRADIENT_ICE",
    "GRADIENT_SOFT",
    "GRADIENT_EMERALD",
    # ── 第五阶段调色板 ──
    "GRADIENT_ROSE",
    "GRADIENT_LAVA",
    "GRADIENT_GLACIER",
    "GRADIENT_SUNSET2",
    "GRADIENT_NEON_GREEN",
    "GRADIENT_NEON_PINK",
    "GRADIENT_GOLD",
    "GRADIENT_SKY",
    "GRADIENT_MAGMA",
    "GRADIENT_OCEAN_DEEP",
    # ── 呼吸调色板 ──
    "BREATH_CYAN",
    "BREATH_GREEN",
    "BREATH_PURPLE",
    "BREATH_GOLD",
    "BREATH_ROSE",
]
