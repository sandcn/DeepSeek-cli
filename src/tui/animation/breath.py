"""呼吸颜色注册表 — BreathPalette。

提供 BreathPalette 集中管理所有呼吸颜色序列。
模块加载时自动注册所有预定义调色板。

从 src/tui/core/animator.py 拆分出 BreathPalette，作为 Layer 0.5
动画基础设施层的核心组件。

增强（2026-07-12）：
  - get_sine_color(): 基于正弦波插值的颜色获取，更平滑
  - 支持 low_high 参数，动态计算而非查表
"""

from __future__ import annotations

from typing import Optional

from ...ui.colors import (
    BREATH_CYAN, BREATH_GREEN, BREATH_PURPLE, BREATH_GOLD, BREATH_ROSE,
    gradient_range,
)

__all__ = [
    "BreathPalette",
]


class BreathPalette:
    """呼吸调色板注册表 — 所有呼吸颜色序列集中管理。

    使用命名查找，消除 12+ 处重复定义。
    模块加载时自动注册所有预定义调色板。
    线程安全：所有操作为只读字典访问 + 纯函数。

    增强（2026-07-12）：
      - get_sine_color(): 基于正弦波插值的颜色获取，更平滑
      - 支持 low_high 参数，动态计算而非查表
    """

    _palettes: dict[str, list[int]] = {}

    @classmethod
    def register(cls, name: str, colors: list[int]) -> None:
        """注册命名调色板。"""
        cls._palettes[name] = list(colors)  # 防御性拷贝

    @classmethod
    def register_many(cls, palettes: dict[str, list[int]]) -> None:
        """批量注册。"""
        for name, colors in palettes.items():
            cls._palettes[name] = list(colors)

    @classmethod
    def get(cls, name: str) -> list[int]:
        """获取调色板颜色列表。不存在时返回空列表。"""
        return cls._palettes.get(name, [])

    @classmethod
    def get_color(cls, name: str, frame: int = 0) -> int:
        """获取指定调色板的当前帧色号。自动取模。"""
        colors = cls._palettes.get(name)
        if not colors:
            return 45  # 兜底色 = CYAN_256
        return colors[frame % len(colors)]

    @classmethod
    def get_sine_color(cls, name: str, frame: int = 0, period: int | None = None) -> int:
        """基于正弦波插值的呼吸色号，比 get_color() 更平滑。

        使用正弦波在调色板色号间插值，两端有自然减速。
        适合需要"有机呼吸"感的场景（分隔线、标签等）。

        Args:
            name: 调色板名称。
            frame: 当前帧号。
            period: 呼吸周期，None 时使用调色板长度。

        Returns:
            插值后的色号（0-255）。
        """
        from ..core.effects import sine_color_range
        colors = cls._palettes.get(name)
        if not colors:
            return 45
        return sine_color_range(frame, colors, period)

    @classmethod
    def has(cls, name: str) -> bool:
        """检查调色板是否存在。"""
        return name in cls._palettes


# ════════════════════════════════════════════════════════
# 预注册调色板（模块加载时自动注册）
# ════════════════════════════════════════════════════════

BreathPalette.register_many({
    # ── 呼吸分隔线（思考/消息/提示符共享同一序列） ──
    "think":      gradient_range(24, 87, 6) + gradient_range(87, 24, 6),
    "sep_msg":    gradient_range(24, 87, 6) + gradient_range(87, 24, 6),
    "prompt":     gradient_range(24, 87, 6) + gradient_range(87, 24, 6),

    # ── 角色呼吸 ──
    "role_user":  gradient_range(45, 81, 4) + gradient_range(81, 45, 4),
    "role_asst":  gradient_range(41, 47, 4) + gradient_range(47, 41, 4),
    "role_tool":  gradient_range(221, 227, 4) + gradient_range(227, 221, 4),

    # ── 底部栏分隔线呼吸 ──
    "sep_bar":    [45, 44, 43, 42, 41, 40, 41, 42, 43, 44],

    # ── 补全弹窗背景呼吸 ──
    "breath_bg":  [235, 236, 237, 238, 239, 240, 239, 238, 237, 236],

    # ── 工具图标脉动 ──
    "tool_pulse": ([214, 216, 218, 220, 218, 216] * 2),

    # ── Agent标题呼吸偏移 ──
    "agent_breath": ([0, 1, 2, 3, 2, 1] * 2),

    # ── 进度条渐变 ──
    "progress_amber_green": gradient_range(214, 41, 8),

    # ── 状态栏脉动 ──
    "pulse":      gradient_range(36, 45, 3) + [40],

    # ── 模型名呼吸 ──
    "model":      [32, 45, 40, 45],

    # ── 错误/告警脉冲 ──
    "error_pulse":  gradient_range(196, 9, 3) + gradient_range(9, 196, 3),
    "warn_pulse":   gradient_range(214, 11, 3) + gradient_range(11, 214, 3),

    # ── 状态栏脉动 ──
    "status_pulse": gradient_range(45, 81, 4) + gradient_range(81, 45, 4),

    # ── 呼吸调色板（2026-07-12 新增） ──
    "breath_cyan": BREATH_CYAN,
    "breath_green": BREATH_GREEN,
    "breath_purple": BREATH_PURPLE,
    "breath_gold": BREATH_GOLD,
    "breath_rose": BREATH_ROSE,
})
