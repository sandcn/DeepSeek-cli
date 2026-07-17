"""闪烁/脉冲/高亮效果子模块 — 从 effects.py 拆分。

包含：闪烁亮度值、闪烁色号计算。
"""

from __future__ import annotations


def sparkle_brightness(frame: int, period: int = 6) -> float:
    """闪烁亮度值 [0.0, 1.0]，模拟星光闪烁。

    使用快起慢落的不对称波形，更自然的闪烁视觉。

    Args:
        frame: 当前帧号。
        period: 闪烁周期帧数。

    Returns:
        [0.0, 1.0] 亮度值。
    """
    t = (frame % period) / period
    # 快速上升（0→0.5t），缓慢下降（0.5→1t）
    if t < 0.3:
        return t / 0.3  # 快速亮起
    else:
        return 1.0 - (t - 0.3) / 0.7  # 缓慢熄灭


def sparkle_color(frame: int, base_color: int = 45, bright_boost: int = 30, period: int = 6) -> int:
    """闪烁色号：在 base_color 和 base_color+bright_boost 间闪烁。

    Args:
        frame: 当前帧号。
        base_color: 基准色号。
        bright_boost: 高亮增量。
        period: 闪烁周期。

    Returns:
        闪烁后的色号。
    """
    t = sparkle_brightness(frame, period)
    return round(base_color + t * bright_boost)


__all__ = [
    "sparkle_brightness",
    "sparkle_color",
]
