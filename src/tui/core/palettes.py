"""
预定义渐变调色板与呼吸调色板常量。

从 :mod:`src.ui.colors` 提取，依赖 :mod:`src.tui.core.gradient` 中的
``gradient_range`` 函数在模块加载时计算色号列表。

所有调色板常量均为 ``list[int]`` 类型，可直接用于 :class:`GradientDescriptor`
和动画效果。
"""
from __future__ import annotations

from .gradient import gradient_range

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

# ════════════════════════════════════════════════════════
# 核心调色板
# ════════════════════════════════════════════════════════

GRADIENT_SUNSET: list[int] = gradient_range(196, 224, 8)
"""日落渐变：红色(196)→琥珀色(224)，8 阶。"""

GRADIENT_OCEAN: list[int] = gradient_range(26, 87, 6)
"""海洋渐变：深蓝(26)→青色(87)，6 阶。"""

GRADIENT_FOREST: list[int] = gradient_range(22, 47, 6)
"""森林渐变：深绿(22)→亮绿(47)，6 阶。"""

GRADIENT_FIRE: list[int] = gradient_range(52, 220, 9)
"""火焰渐变：深红(52)→亮黄(220)，9 阶。"""

GRADIENT_NEON: list[int] = [
    57, 93, 129, 165, 171, 177, 183, 189, 195, 87
]
"""霓虹渐变：紫(57)→粉→青(87)，10 阶（非均匀插值，手工精选）。"""

GRADIENT_AURORA: list[int] = gradient_range(57, 47, 8)
"""极光渐变：紫蓝(57)→亮绿(47)，8 阶。"""

GRADIENT_CORAL: list[int] = gradient_range(203, 224, 6)
"""珊瑚渐变：珊瑚红(203)→米白(224)，6 阶。"""

GRADIENT_MINT: list[int] = gradient_range(29, 114, 6)
"""薄荷渐变：深青绿(29)→柔和绿(114)，6 阶。"""

GRADIENT_TWILIGHT: list[int] = gradient_range(53, 195, 8)
"""暮光渐变：深紫(53)→亮青(195)，8 阶。"""

# ════════════════════════════════════════════════════════
# 第四阶段调色板
# ════════════════════════════════════════════════════════

GRADIENT_SUNRISE: list[int] = gradient_range(208, 220, 8)
"""日出渐变：暖橙(208)→亮黄(220)，8 阶。"""

GRADIENT_PURPLE: list[int] = gradient_range(55, 177, 8)
"""紫渐变：深紫(55)→亮紫(177)，8 阶。"""

GRADIENT_ICE: list[int] = gradient_range(24, 87, 8)
"""冰蓝渐变：深蓝(24)→亮青(87)，8 阶。"""

GRADIENT_SOFT: list[int] = gradient_range(175, 218, 8)
"""柔和粉渐变：粉红(175)→亮粉(218)，8 阶。"""

GRADIENT_EMERALD: list[int] = gradient_range(22, 47, 8)
"""翡翠渐变：深绿(22)→亮绿(47)，8 阶。"""

# ════════════════════════════════════════════════════════
# 第五阶段调色板
# ════════════════════════════════════════════════════════

GRADIENT_ROSE: list[int] = gradient_range(161, 218, 8)
"""玫瑰渐变：玫红(161)→亮粉(218)，8 阶。"""

GRADIENT_LAVA: list[int] = gradient_range(52, 220, 10)
"""熔岩渐变：深红(52)→亮黄(220)，10 阶。"""

GRADIENT_GLACIER: list[int] = gradient_range(32, 195, 8)
"""冰河渐变：深蓝(32)→亮青(195)，8 阶。"""

GRADIENT_SUNSET2: list[int] = gradient_range(53, 224, 10)
"""日落强化渐变：深紫(53)→琥珀(224)，10 阶。"""

GRADIENT_NEON_GREEN: list[int] = gradient_range(40, 83, 8)
"""霓虹绿渐变：中绿(40)→亮青绿(83)，8 阶。"""

GRADIENT_NEON_PINK: list[int] = gradient_range(125, 213, 8)
"""霓虹粉渐变：暗粉(125)→亮粉(213)，8 阶。"""

GRADIENT_GOLD: list[int] = gradient_range(94, 220, 8)
"""金色渐变：暗金(94)→亮金(220)，8 阶。"""

GRADIENT_SKY: list[int] = gradient_range(25, 117, 8)
"""天空渐变：深蓝(25)→天蓝(117)，8 阶。"""

GRADIENT_MAGMA: list[int] = gradient_range(88, 202, 10)
"""岩浆渐变：深红褐(88)→亮橙(202)，10 阶。"""

GRADIENT_OCEAN_DEEP: list[int] = gradient_range(17, 44, 8)
"""深海渐变：深蓝(17)→海青(44)，8 阶。"""

# ════════════════════════════════════════════════════════
# 呼吸调色板（对称上升+下降）
# ════════════════════════════════════════════════════════

BREATH_CYAN: list[int] = gradient_range(24, 87, 6) + gradient_range(87, 24, 6)
"""青呼吸：深蓝(24)→亮青(87)→深蓝(24)，12 阶对称。"""

BREATH_GREEN: list[int] = gradient_range(22, 47, 6) + gradient_range(47, 22, 6)
"""绿呼吸：深绿(22)→亮绿(47)→深绿(22)，12 阶对称。"""

BREATH_PURPLE: list[int] = gradient_range(55, 177, 6) + gradient_range(177, 55, 6)
"""紫呼吸：深紫(55)→亮紫(177)→深紫(55)，12 阶对称。"""

BREATH_GOLD: list[int] = gradient_range(94, 220, 6) + gradient_range(220, 94, 6)
"""金呼吸：暗金(94)→亮金(220)→暗金(94)，12 阶对称。"""

BREATH_ROSE: list[int] = gradient_range(161, 218, 6) + gradient_range(218, 161, 6)
"""玫瑰呼吸：玫红(161)→亮粉(218)→玫红(161)，12 阶对称。"""
