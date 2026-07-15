"""正弦波/呼吸/波动效果原语 — 纯函数实现。

包含：
  - sine_* 系列：正弦波呼吸平滑过渡
  - bounce_* 系列：弹入缓动曲线
  - wave_* 系列：波动渐变动效
  - build_fade_in_ansi_enhanced / build_wave_sep_ansi 便捷构建器

设计原则：
  - 纯函数：输入帧号 → 输出值/ANSI字符串，无副作用
  - 可缓存：热点动效使用 @lru_cache 减少重复计算
  - 窄屏安全：所有 ANSI 生成函数检查窄屏条件
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Sequence


# ═══════════════════════════════════════════════════════════
# 正弦波呼吸工具
# ═══════════════════════════════════════════════════════════


def sine_breath_t(frame: int, period: int = 12) -> float:
    """正弦波呼吸归一化值 [0.0, 1.0]，平滑缓入缓出。

    使用 ``sin(phase - π/2)`` 将 -1→1→-1 映射为 0→1→0，
    在 min 和 max 处具有自然减速（导数趋近0），
    比线性步进的"硬切"变更加柔和。

    Args:
        frame: 当前帧号（单调递增）。
        period: 呼吸周期帧数，默认 12 帧（10Hz 下约 1.2s）。

    Returns:
        [0.0, 1.0] 范围的正弦波值，1.0=最亮，0.0=最暗。
    """
    phase = (frame % period) / period * 2.0 * math.pi
    return (math.sin(phase - math.pi / 2.0) + 1.0) / 2.0


def sine_color(frame: int, color_low: int, color_high: int, period: int = 12) -> int:
    """正弦波插值色号，在 color_low ↔ color_high 间平滑过渡。

    比线性列表（BreathPalette.get_color）更平滑，
    因为正弦波在两端有自然减速。

    Args:
        frame: 当前帧号。
        color_low: 最暗色号（0-255）。
        color_high: 最亮色号（0-255）。
        period: 呼吸周期帧数。

    Returns:
        [color_low, color_high] 范围的色号，四舍五入取整。
    """
    t = sine_breath_t(frame, period)
    return round(color_low + t * (color_high - color_low))


def sine_color_range(
    frame: int, colors: Sequence[int], period: int | None = None,
) -> int:
    """对任意长度颜色列表做正弦波插值取色。

    相比取模索引（线性跳变），正弦波插值在列表两端有缓入缓出。

    Args:
        frame: 当前帧号。
        colors: 颜色列表。
        period: 呼吸周期帧数，None 时自动设为 len(colors)。

    Returns:
        插值后的色号。
    """
    n = len(colors)
    if n == 0:
        return 45  # 兜底
    if n == 1:
        return colors[0]
    p = period if period is not None else n
    t = sine_breath_t(frame, p)
    idx_f = t * (n - 1)
    idx_low = int(idx_f)
    idx_high = min(idx_low + 1, n - 1)
    frac = idx_f - idx_low
    c_low, c_high = colors[idx_low], colors[idx_high]
    return round(c_low + frac * (c_high - c_low))


# ═══════════════════════════════════════════════════════════
# 弹入缓动曲线
# ═══════════════════════════════════════════════════════════


def bounce_easing(t: float) -> float:
    """弹入缓动曲线（类似 CSS ease-out-bounce）。

    0.0→1.0 过程中超调至 1.1 再回弹至 1.0，
    模拟物体落地的物理弹跳感。

    Args:
        t: 归一化时间 [0.0, 1.0]。

    Returns:
        弹入值 [0.0, ~1.1]，超调后稳定在 1.0。
    """
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    return 1.0 - (1.0 - t) ** 2 + 0.12 * math.sin(t * math.pi * 5.0) * (1.0 - t)


@lru_cache(maxsize=32)
def _precompute_bounce_sequence(total_frames: int) -> list[int]:
    """预计算弹入动效的帧亮度序列。

    亮度从 238（暗灰）渐升至 255（最亮），
    带弹跳超调（比目标亮 → 回落 → 稳定）。

    Args:
        total_frames: 弹入总帧数。

    Returns:
        len=total_frames 的色号列表，索引=帧号。
    """
    result: list[int] = []
    for f in range(total_frames):
        t = f / max(total_frames - 1, 1)
        eased = bounce_easing(t)
        color = round(238 + eased * (255 - 238))
        result.append(max(0, min(255, color)))
    return result


def bounce_frame_color(frame: int, total_frames: int = 8) -> int:
    """获取弹入动效的帧色号。

    Args:
        frame: 当前帧号（0-based）。
        total_frames: 弹入总帧数。

    Returns:
        色号（0-255），≥ total_frames 时返回 255（全亮稳态）。
    """
    if frame >= total_frames:
        return 255
    seq = _precompute_bounce_sequence(total_frames)
    return seq[frame] if frame < len(seq) else 255


# ═══════════════════════════════════════════════════════════
# 波动效果
# ═══════════════════════════════════════════════════════════


def wave_offset(index: int, frame: int, amplitude: float = 3.0, wavelength: float = 4.0) -> float:
    """计算单个位置的正弦波动偏移量。

    用于对渐变分隔线等线性排列元素施加波动效果，
    使其看起来像"流动"或"呼吸"。

    Args:
        index: 在序列中的位置索引。
        frame: 当前帧号。
        amplitude: 波动幅度（色号偏移量）。
        wavelength: 波长（以字符数为单位）。

    Returns:
        偏移量（浮点数，需 round 后应用）。
    """
    return math.sin((index + frame * 0.5) * 2.0 * math.pi / wavelength) * amplitude


def apply_wave(colors: list[int], frame: int, amplitude: float = 3.0, wavelength: float = 4.0) -> list[int]:
    """对颜色列表施加波动效果。

    每个位置的色号叠加正弦偏移，形成沿序列传播的"波浪"效果。
    帧号推进时波浪沿序列方向移动。

    Args:
        colors: 原始颜色列表。
        frame: 当前帧号。
        amplitude: 波动幅度（色号偏移）。
        wavelength: 波长（字符数）。

    Returns:
        波动后的新颜色列表（不修改原始列表）。
    """
    result: list[int] = []
    for i, c in enumerate(colors):
        offset = round(wave_offset(i, frame, amplitude, wavelength))
        result.append(max(0, min(255, c + offset)))
    return result


# ═══════════════════════════════════════════════════════════
# ANSI 序列构建（便捷包装器）
# ═══════════════════════════════════════════════════════════


def build_fade_in_ansi_enhanced(frame: int, total_frames: int = 4, style: str = "smooth",
                                 narrow: bool = False) -> str:
    """增强版渐显 ANSI 序列（支持弹入和正弦平滑）。

    Args:
        frame: 当前渐显帧号（0-based）。
        total_frames: 渐显总帧数。
        style: "smooth"（正弦平滑）| "bounce"（弹跳）| "linear"（线性）。
        narrow: 窄屏模式（调用方传入 is_narrow() 结果），窄屏时返回空。

    Returns:
        ANSI 颜色序列，≥ total_frames 时返回空字符串。
    """
    if narrow or frame >= total_frames:
        return ""
    if style == "bounce":
        color = bounce_frame_color(frame, total_frames)
    elif style == "smooth":
        t = sine_breath_t(frame, total_frames)
        color = round(238 + t * (255 - 238))
    else:  # linear
        t = frame / max(total_frames - 1, 1)
        color = round(238 + t * (255 - 238))
    return f"\033[38;5;{min(255, max(0, color))}m"


def build_wave_sep_ansi(colors: list[int], frame: int, char: str = "\u2501", amplitude: float = 2.0) -> str:
    """构建波动分隔线 ANSI 字符串。

    结合渐变和波动效果，使分隔线像"流动的水波"。

    Args:
        colors: 基础渐变色号列表。
        frame: 当前帧号。
        char: 显示的字符。
        amplitude: 波动幅度。

    Returns:
        ANSI 格式的波动渐变分隔线。
    """
    waved = apply_wave(colors, frame, amplitude=amplitude)
    parts = [f"\033[38;5;{c}m{char}" for c in waved]
    return "".join(parts) + "\033[0m"


__all__ = [
    "sine_breath_t", "sine_color", "sine_color_range",
    "bounce_easing", "bounce_frame_color",
    "wave_offset", "apply_wave",
    "build_fade_in_ansi_enhanced",
    "build_wave_sep_ansi",
]
