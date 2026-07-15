"""列车/扫光/流动效果原语 — 纯函数实现。

包含：
  - rainbow_* 系列：彩虹渐变旋转效果
  - pulse_train / build_pulse_train_ansi：脉冲列车效果
  - matrix_rain_* 系列：矩阵数字雨效果
  - heat_wave_* 系列：热浪扭曲效果
  - aurora_* 系列：极光飘动效果

设计原则：
  - 纯函数：输入帧号 → 输出值/ANSI字符串，无副作用
  - 可缓存：热点动效使用 @lru_cache 减少重复计算
"""

from __future__ import annotations

import math


# ═══════════════════════════════════════════════════════════
# 彩虹渐变效果
# ═══════════════════════════════════════════════════════════


def rainbow_color(frame: int, index: int, num_steps: int = 12) -> int:
    """彩虹渐变效果 — 在色环上产生旋转颜色。

    将色环映射到 256 色空间（红→橙→黄→绿→蓝→紫→红），
    frame 控制整体旋转，index 控制序列位置。

    Args:
        frame: 当前帧号，控制彩虹旋转相位
        index: 在序列中的位置（0 到 num_steps-1）
        num_steps: 彩虹总步数

    Returns:
        xterm-256 色号（0-255）
    """
    RAINBOW_CYCLE = [196, 208, 220, 46, 87, 33, 129, 199]
    cycle_len = len(RAINBOW_CYCLE)
    pos = (index + frame) % cycle_len
    return RAINBOW_CYCLE[pos]


def build_rainbow_ansi(text: str, frame: int) -> str:
    """构建彩虹色 ANSI 序列。

    每个字符使用 RAINBOW_CYCLE 中不同颜色，frame 控制旋转。

    Args:
        text: 要渲染的文本
        frame: 当前帧号

    Returns:
        带 ANSI 颜色的文本
    """
    from .ansi_utils import visual_width
    result: list[str] = []
    vpos = 0
    for ch in text:
        color = rainbow_color(frame, vpos, 8)
        result.append(f"\033[38;5;{color}m{ch}")
        vw = visual_width(ch)
        vpos += vw if vw > 0 else 1
    result.append("\033[0m")
    return "".join(result)


# ═══════════════════════════════════════════════════════════
# 脉冲列车效果
# ═══════════════════════════════════════════════════════════


def pulse_train(frame: int, pos: int, total_width: int,
                color_low: int = 239, color_high: int = 45,
                num_pulses: int = 2) -> int:
    """脉冲列车效果 — 沿序列方向产生多个脉动波峰。

    使用高斯形状脉冲在序列中传播，模拟心跳脉冲列。

    Args:
        frame: 当前帧号，控制脉冲前进位置
        pos: 当前位置（0 到 total_width-1）
        total_width: 序列总宽度
        color_low: 基础色号（波谷）
        color_high: 脉冲峰值色号
        num_pulses: 脉冲数量

    Returns:
        当前位置的色号
    """
    if total_width <= 0:
        return color_low
    pulse_width = max(1.0, total_width / max(num_pulses, 1))
    offset = (frame * 2) % total_width
    rel_pos = (pos + offset) % total_width
    min_dist = pulse_width / 2
    for p in range(num_pulses):
        center = p * pulse_width
        dist = abs(rel_pos - center)
        if dist < min_dist:
            min_dist = dist
    sigma = pulse_width / (num_pulses * 2.0)
    if sigma <= 0:
        return color_high
    intensity = max(0.0, min(1.0, math.exp(-(min_dist ** 2) / (2 * sigma ** 2))))
    color_val = int(color_low + (color_high - color_low) * intensity)
    return max(0, min(255, color_val))


def build_pulse_train_ansi(width: int, frame: int,
                           color_low: int = 239, color_high: int = 45,
                           char: str = "━") -> str:
    """构建脉冲列车渐变 ANSI 分隔线。

    Args:
        width: 分隔线宽度
        frame: 当前帧号
        color_low: 基础色号
        color_high: 脉冲色号
        char: 分隔线字符

    Returns:
        带 ANSI 颜色的分隔线
    """
    result: list[str] = []
    for i in range(width):
        color = pulse_train(frame, i, width, color_low, color_high)
        result.append(f"\033[38;5;{color}m{char}")
    result.append("\033[0m")
    return "".join(result)


# ═══════════════════════════════════════════════════════════
# 矩阵数字雨效果
# ═══════════════════════════════════════════════════════════


def matrix_rain_color(frame: int, pos: int, total_height: int,
                       speed: float = 0.3) -> int:
    """矩阵数字雨效果 — 模拟《黑客帝国》数字雨下落。

    亮度随位置和帧号变化，头部亮绿→尾部暗绿。

    Args:
        frame: 当前帧号
        pos: 当前位置
        total_height: 总高度
        speed: 下落速度因子

    Returns:
        xterm-256 色号
    """
    if total_height <= 0:
        return 40
    drop_pos = (frame * speed * 2) % (total_height * 2)
    if drop_pos > total_height:
        drop_pos = 2 * total_height - drop_pos  # 折返
    dist = abs(pos - drop_pos)
    ratio = max(0.0, 1.0 - dist / max(total_height, 1))
    color_val = int(22 + (46 - 22) * ratio)
    return max(0, min(255, color_val))


def build_matrix_rain_ansi(text: str, frame: int, speed: float = 0.3) -> str:
    """构建矩阵数字雨 ANSI 文本。

    Args:
        text: 要渲染的文本
        frame: 当前帧号
        speed: 下落速度

    Returns:
        带 ANSI 颜色的文本
    """
    result: list[str] = []
    total = len(text)
    for i, ch in enumerate(text):
        color = matrix_rain_color(frame, i, total, speed)
        result.append(f"\033[38;5;{color}m{ch}")
    result.append("\033[0m")
    return "".join(result)


# ═══════════════════════════════════════════════════════════
# 热浪扭曲效果
# ═══════════════════════════════════════════════════════════


def heat_wave_offset(pos: int, frame: int, amplitude: float = 5.0,
                      frequency: float = 0.3) -> int:
    """热浪扭曲效果 — 色号在基础色周围波动。

    使用正弦波混叠产生类似热空气扭曲的视觉效果。

    Args:
        pos: 序列位置
        frame: 当前帧号
        amplitude: 波动幅度
        frequency: 波动频率

    Returns:
        相对于基础色的偏移量
    """
    offset = int(amplitude * math.sin(pos * frequency + frame * 0.15))
    return offset


def apply_heat_wave(base_color: int, pos: int, frame: int,
                     amplitude: float = 5.0, frequency: float = 0.3) -> int:
    """对基础色号施加热浪扭曲偏移。

    Args:
        base_color: 基础色号（0-255）
        pos: 序列位置
        frame: 当前帧号
        amplitude: 波动幅度
        frequency: 波动频率

    Returns:
        偏移后的色号
    """
    offset = heat_wave_offset(pos, frame, amplitude, frequency)
    return max(0, min(255, base_color + offset))


def build_heat_wave_ansi(colors: list[int], frame: int,
                          amplitude: float = 5.0, char: str = "━") -> str:
    """构建热浪扭曲 ANSI 序列。

    Args:
        colors: 基础色号列表
        frame: 当前帧号
        amplitude: 热浪幅度
        char: 分隔线字符

    Returns:
        带 ANSI 颜色的序列
    """
    result: list[str] = []
    for i, base_color in enumerate(colors):
        c = apply_heat_wave(base_color, i, frame, amplitude)
        result.append(f"\033[38;5;{c}m{char}")
    result.append("\033[0m")
    return "".join(result)


# ═══════════════════════════════════════════════════════════
# 极光飘动效果
# ═══════════════════════════════════════════════════════════


def aurora_color(frame: int, pos: int, colors: list[int],
                  speed: float = 0.2) -> int:
    """极光飘动效果 — 模拟极光在天空中的缓慢飘动。

    多色渐变层在时间和空间维度上交错移动，产生极光般的变化。

    Args:
        frame: 当前帧号
        pos: 序列位置（0 到 colors_len-1）
        colors: 基础色号列表（渐变调色板）
        speed: 飘动速度

    Returns:
        当前位置的色号
    """
    if not colors:
        return 45
    n = len(colors)
    wave1 = math.sin(pos * 0.3 + frame * speed)
    wave2 = math.sin(pos * 0.15 + frame * speed * 0.7)
    wave3 = math.sin(pos * 0.5 + frame * speed * 1.3)
    blend = (wave1 * 0.5 + wave2 * 0.3 + wave3 * 0.2)
    idx = int((blend + 1.0) / 2.0 * (n - 1))
    idx = max(0, min(n - 1, idx))
    return colors[idx]


def build_aurora_gradient(width: int, frame: int,
                           colors: list[int] | None = None) -> list[int]:
    """生成极光渐变色号列表。

    Args:
        width: 序列宽度
        frame: 当前帧号
        colors: 基础调色板（默认使用 GRADIENT_AURORA）

    Returns:
        色号列表
    """
    if colors is None:
        from .palettes import GRADIENT_AURORA
        colors = GRADIENT_AURORA
    return [aurora_color(frame, i, colors) for i in range(width)]


def build_aurora_ansi(width: int, frame: int,
                       colors: list[int] | None = None, char: str = "━") -> str:
    """构建极光飘动 ANSI 序列。

    Args:
        width: 序列宽度
        frame: 当前帧号
        colors: 基础调色板
        char: 分隔线字符

    Returns:
        带 ANSI 颜色的序列
    """
    result: list[int] = build_aurora_gradient(width, frame, colors)
    parts: list[str] = []
    for c in result:
        parts.append(f"\033[38;5;{c}m{char}")
    parts.append("\033[0m")
    return "".join(parts)


__all__ = [
    "rainbow_color", "build_rainbow_ansi",
    "pulse_train", "build_pulse_train_ansi",
    "matrix_rain_color", "build_matrix_rain_ansi",
    "heat_wave_offset", "apply_heat_wave", "build_heat_wave_ansi",
    "aurora_color", "build_aurora_gradient", "build_aurora_ansi",
]
