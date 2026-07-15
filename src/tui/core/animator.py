"""统一动画基础设施 — 集中动画时钟管理 + 呼吸颜色注册表。

提供：
  - AnimatorContext: 全局单例动画时钟管理器，统一推进所有动画帧号
  - BreathPalette:   呼吸颜色注册表，集中管理所有呼吸颜色序列

模块加载时自动注册所有预定义调色板。

增强（2026-07-12）：
  - 添加正弦波呼吸属性（sine_breath/sine_pulse/sine_color）
    替代纯线性步进，实现平滑的缓入缓出呼吸效果
  - 所有现有属性（breath_frame/pulse_frame）保持向后兼容
"""
from tui_framework.core.animator import *

__all__ = [
    "AnimatorContext",
    "BreathPalette",
]
