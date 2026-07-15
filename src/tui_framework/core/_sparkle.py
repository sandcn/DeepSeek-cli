"""闪烁/脉冲/高亮/辉光效果原语 — 纯函数实现。

包含：
  - sparkle_* 系列：星光闪烁效果
  - shimmer_* 系列：流光扫光效果
  - build_glow_ansi / build_fg_breath_ansi / build_bg_breath_ansi 辉光呼吸 ANSI 构建器
  - get_theme_effect_color 主题动效色消费者

设计原则：
  - 纯函数：输入帧号 → 输出值/ANSI字符串，无副作用
  - 可缓存：热点动效使用 @lru_cache 减少重复计算
"""

from __future__ import annotations

import re

from ._wave import sine_color


# ═══════════════════════════════════════════════════════════
# 闪烁高亮效果
# ═══════════════════════════════════════════════════════════


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


# ═══════════════════════════════════════════════════════════
# 流光扫光效果（Shimmer）
# ═══════════════════════════════════════════════════════════


def shimmer_position(frame: int, total_width: int, speed: float = 0.5) -> float:
    """计算流光扫光的当前位置。

    一个亮带沿宽度方向周期性移动，类似"扫光"效果。

    Args:
        frame: 当前帧号。
        total_width: 总宽度。
        speed: 移动速度（宽度/帧）。

    Returns:
        亮带中心位置 [0, total_width)。
    """
    return (frame * speed) % total_width


def shimmer_apply(
    colors: list[int], frame: int, width: int = 5, speed: float = 0.5,
    boost: int = 40,
) -> list[int]:
    """对颜色列表施加流光扫光效果。

    一个亮带沿序列方向移动，亮带内色号增加 boost。

    Args:
        colors: 原始颜色列表。
        frame: 当前帧号。
        width: 亮带宽度（字符数）。
        speed: 移动速度（字符/帧）。
        boost: 亮带内色号增量。

    Returns:
        扫光后的新颜色列表。
    """
    n = len(colors)
    center = shimmer_position(frame, n, speed)
    result: list[int] = []
    for i in range(n):
        dist = abs(i - center)
        if dist < width:
            factor = 1.0 - dist / width
            result.append(max(0, min(255, colors[i] + round(boost * factor))))
        else:
            result.append(colors[i])
    return result


# ═══════════════════════════════════════════════════════════
# ANSI 序列构建
# ═══════════════════════════════════════════════════════════


def build_shimmer_sep_ansi(colors: list[int], frame: int, char: str = "\u2501") -> str:
    """构建流光扫光分隔线 ANSI 字符串。

    一条亮带沿分隔线方向周期性移动。

    Args:
        colors: 基础渐变色号列表。
        frame: 当前帧号。
        char: 显示的字符。

    Returns:
        ANSI 格式的流光分隔线。
    """
    shimmered = shimmer_apply(colors, frame)
    parts = [f"\033[38;5;{c}m{char}" for c in shimmered]
    return "".join(parts) + "\033[0m"


# ═══════════════════════════════════════════════════════════
# 便捷工具：ANSI 装饰生成
# ═══════════════════════════════════════════════════════════


def build_glow_ansi(frame: int, base_color: int = 45, period: int = 12) -> str:
    """构建辉光呼吸 ANSI 序列。

    色号在 base_color 和 base_color+20 间正弦呼吸，
    适合用于标签、边框等需要"发光"效果的元素。

    Args:
        frame: 当前帧号。
        base_color: 基准色号。
        period: 呼吸周期。

    Returns:
        ANSI 前景色序列。
    """
    c = sine_color(frame, base_color, min(255, base_color + 20), period)
    return f"\033[38;5;{c}m"


def build_fg_breath_ansi(frame: int, color_low: int, color_high: int, period: int = 12) -> str:
    """构建呼吸前景色 ANSI 序列。

    在 color_low 和 color_high 间正弦呼吸。

    Args:
        frame: 当前帧号。
        color_low: 最暗色号。
        color_high: 最亮色号。
        period: 呼吸周期。

    Returns:
        ANSI 前景色序列。
    """
    c = sine_color(frame, color_low, color_high, period)
    return f"\033[38;5;{c}m"


def build_bg_breath_ansi(frame: int, color_low: int, color_high: int, period: int = 12) -> str:
    """构建呼吸背景色 ANSI 序列。

    在 color_low 和 color_high 间正弦呼吸。

    Args:
        frame: 当前帧号。
        color_low: 最暗色号。
        color_high: 最亮色号。
        period: 呼吸周期。

    Returns:
        ANSI 背景色序列。
    """
    c = sine_color(frame, color_low, color_high, period)
    return f"\033[48;5;{c}m"


# ═══════════════════════════════════════════════════════════
# 主题动效色消费者（effect_* 键）
# ═══════════════════════════════════════════════════════════


def get_theme_effect_color(effect_name: str, frame: int = 0) -> str:
    """从当前主题获取动效色，支持正弦波呼吸增强。

    Args:
        effect_name: 动效名称（"bounce"/"wave"/"sparkle"/"glow"/"shimmer"）。
        frame: 帧号，>0 时对 glow/sparkle 叠加正弦波呼吸。

    Returns:
        ANSI 前景色序列。
    """
    from .theme import THEME
    key = f"effect_{effect_name}"
    base = THEME.get(key, None)
    if base is None:
        return _build_fg_ansi(45)  # 兜底青色
    if frame > 0 and effect_name in ("glow", "sparkle"):
        m = re.search(r"38;5;(\d+)", base)
        if m:
            base_color = int(m.group(1))
            breath_color = sine_color(frame, base_color, min(255, base_color + 15), 12)
            return f"\033[38;5;{breath_color}m"
    return base


def _build_fg_ansi(color: int) -> str:
    """构建前景色 ANSI 序列。

    Args:
        color: 256 色号（0-255）。

    Returns:
        ANSI 前景色序列。
    """
    return f"\033[38;5;{color}m"


__all__ = [
    "sparkle_brightness", "sparkle_color",
    "shimmer_position", "shimmer_apply",
    "build_shimmer_sep_ansi",
    "build_glow_ansi",
    "build_fg_breath_ansi",
    "build_bg_breath_ansi",
    "get_theme_effect_color",
]
