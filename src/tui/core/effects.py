"""动效原语模块 — 所有动画效果的纯函数实现。

集中管理所有动画动效的计算逻辑，消除散落在各显示组件中
的重复实现。所有函数为纯计算，不依赖 AnimatorContext/
BreathPalette，直接接受帧号作为参数，可独立测试。

【已重构】effects.py 已按效果类别拆分为 4 个子模块（2026-07-17）：
  - _wave.py — 呼吸/正弦/波动效果（sine_color, build_glow_ansi 等）
  - _sparkle.py — 闪烁/脉冲/高亮效果（sparkle_color 等）
  - _train.py — 列车/扫光/流动效果（build_pulse_train_ansi 等）
  - _compose.py — EffectRegistry 合成器与效果包装
  effects.py 保留为统一重导出入口，保持向后兼容。
  新代码可直接从子模块导入以获得更精确的依赖。

设计原则：
  - 纯函数：输入帧号 → 输出值/ANSI字符串，无副作用
  - 可缓存：热点动效使用 @lru_cache 减少重复计算
  - 窄屏安全：所有 ANSI 生成函数检查窄屏条件
  - 无 I/O：不涉及终端写入，仅生成 ANSI 序列
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Callable, ClassVar, Sequence

from .color import to_ansi_fg


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
    # sin(phase - π/2) 在 phase=0 时为 -1，phase=π 时为 1
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
    # 衰减正弦波超调
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
        # 亮度映射：238(暗) → 255(最亮)，超调可达 257→clamp
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


def build_fade_in_ansi_enhanced(frame: int, total_frames: int = 4, style: str = "smooth") -> str:
    """增强版渐显 ANSI 序列（支持弹入和正弦平滑）。

    Args:
        frame: 当前渐显帧号（0-based）。
        total_frames: 渐显总帧数。
        style: "smooth"（正弦平滑）| "bounce"（弹跳）| "linear"（线性）。

    Returns:
        ANSI 颜色序列，≥ total_frames 时返回空字符串。
    """
    from ..terminal.terminal import is_narrow
    if is_narrow() or frame >= total_frames:
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


def build_breath_ansi(frame: int, color_low: int, color_high: int, period: int = 12, layer: str = "fg") -> str:
    """构建呼吸 ANSI 序列（前景或背景）。

    在 color_low 和 color_high 间正弦呼吸，
    支持前景色 (38;5) 和背景色 (48;5) 输出。

    Args:
        frame: 当前帧号。
        color_low: 最暗色号。
        color_high: 最亮色号。
        period: 呼吸周期。
        layer: "fg" 前景色 (38;5) 或 "bg" 背景色 (48;5)。

    Returns:
        ANSI 颜色序列。
    """
    c = sine_color(frame, color_low, color_high, period)
    if layer not in ("fg", "bg"):
        layer = "fg"
    ansi_prefix = "38" if layer == "fg" else "48"
    return f"\033[{ansi_prefix};5;{c}m"


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
    return build_breath_ansi(frame, color_low, color_high, period, layer="fg")


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
    return build_breath_ansi(frame, color_low, color_high, period, layer="bg")


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
    import re
    key = f"effect_{effect_name}"
    base = THEME.get(key, None)
    if base is None:
        return to_ansi_fg(45)  # 兜底青色
    if frame > 0 and effect_name in ("glow", "sparkle"):
        m = re.search(r"38;5;(\d+)", base)
        if m:
            base_color = int(m.group(1))
            breath_color = sine_color(frame, base_color, min(255, base_color + 15), 12)
            return f"\033[38;5;{breath_color}m"
    return base


# ═══════════════════════════════════════════════════════════
# FadeIn 缓动因子
# ═══════════════════════════════════════════════════════════


def fade_factor(frame: int, total: int = 6) -> float:
    """平滑缓动渐显因子 [0.0, 1.0]。

    使用正弦缓动（sine_easing）计算渐显程度：
    frame 0 → 0.0（全暗），frame >= total → 1.0（全亮）。
    从 ``_base._fade_factor`` 迁移，统一 FadeIn 缓动入口。

    Args:
        frame: 当前帧号（0-based）。
        total: 渐显总帧数，默认 6 帧（10Hz 下约 0.6s）。

    Returns:
        [0.0, 1.0] 范围的缓动因子。
    """
    if frame <= 0:
        return 0.0
    t = min(frame / total, 1.0)
    return (math.sin((t - 0.5) * math.pi) + 1) / 2


def fade_color(target: int, fade: float, base: int = 238) -> int:
    """将缓动因子融入色号：暗色 → 目标色。

    从 ``_base._fade_color`` 迁移，统一 FadeIn 色号计算入口。

    Args:
        target: 目标色号（0-255）。
        fade: 缓动因子 [0.0, 1.0]。
        base: 起始暗色，默认 238（深灰）。

    Returns:
        插值后的色号，clamp 到 [0, 255]。
    """
    return max(0, min(255, int(base + (target - base) * fade)))


# ═══════════════════════════════════════════════════════════
# 缓动函数（统一入口）
# ═══════════════════════════════════════════════════════════


def sine_easing(t: float) -> float:
    """正弦平滑缓动 [0,1] → [0,1]，两端减速。

    与 ``sine_breath_t`` 同族但语义不同：
    后者是绝对帧号的归一化值，前者是纯缓动因子。
    从 ``transitions._easing_smooth`` 迁移，统一缓动函数入口。

    Args:
        t: 归一化时间 [0.0, 1.0]。

    Returns:
        缓动后的值 [0.0, 1.0]，两端导数趋近0。
    """
    return (math.sin(t * math.pi - math.pi / 2.0) + 1.0) / 2.0

# ═══════════════════════════════════════════════════════════
# 闪烁/脉冲/高亮效果
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
            # 亮带内：中心最亮，向外衰减
            factor = 1.0 - dist / width
            result.append(max(0, min(255, colors[i] + round(boost * factor))))
        else:
            result.append(colors[i])
    return result


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
    # 彩虹色环：红(196)→橙(208)→黄(220)→绿(46)→青(87)→蓝(33)→紫(129)→粉(199)
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
    # 每个脉冲的宽度 = total_width / num_pulses
    pulse_width = max(1.0, total_width / max(num_pulses, 1))
    # 帧号驱动的偏移
    offset = (frame * 2) % total_width
    # 当前位置相对于偏移的距离
    rel_pos = (pos + offset) % total_width
    # 找到最近的脉冲中心
    min_dist = pulse_width / 2
    for p in range(num_pulses):
        center = p * pulse_width
        dist = abs(rel_pos - center)
        if dist < min_dist:
            min_dist = dist
    # 高斯衰减
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
    # 模拟下落位置
    drop_pos = (frame * speed * 2) % (total_height * 2)
    if drop_pos > total_height:
        drop_pos = 2 * total_height - drop_pos  # 折返
    dist = abs(pos - drop_pos)
    # 头部亮绿(46) → 尾部暗绿(22)
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
    # 多层正弦波叠加产生极光般的流动感
    wave1 = math.sin(pos * 0.3 + frame * speed)
    wave2 = math.sin(pos * 0.15 + frame * speed * 0.7)
    wave3 = math.sin(pos * 0.5 + frame * speed * 1.3)
    blend = (wave1 * 0.5 + wave2 * 0.3 + wave3 * 0.2)
    # 映射到调色板索引
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
        from ..animation.palettes import GRADIENT_AURORA
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


# ═══════════════════════════════════════════════════════════
# 霓虹边框效果（neon_border）
# ═══════════════════════════════════════════════════════════


def neon_color(frame: int, base_color: int = 51, spread: int = 3) -> int:
    """霓虹灯管色号摆动 — 模拟霓虹灯管的不稳定发光。

    使用正弦波在 base_color ± spread 范围内漂移，
    每帧颜色微调产生"闪烁"感。

    Args:
        frame: 当前帧号。
        base_color: 基准色号（默认 51=霓虹青色）。
        spread: 摆动幅度（色号范围 ±spread）。

    Returns:
        xterm-256 色号（0-255）。
    """
    offset = round((math.sin(frame * 0.8) + math.sin(frame * 1.3) * 0.5) / 1.5 * spread)
    return max(0, min(255, base_color + offset))


def build_neon_border_ansi(
    text: str, frame: int, base_color: int = 51, width: int | None = None,
) -> str:
    """构建霓虹边框 ANSI 字符串。

    对文本四边包裹霓虹色边框，每帧颜色微调产生"闪烁"感。
    窄屏时降级为单色边框（使用现有呼吸效果替代）。

    边框格式::

        ┌──────────┐
        │  text    │
        └──────────┘

    Args:
        text: 要包裹的文本内容（支持多行）。
        frame: 当前帧号。
        base_color: 霓虹基准色号（默认 51=青色）。
        width: 边框宽度，None 时自动取最长行宽。

    Returns:
        带霓虹边框的 ANSI 字符串。
    """
    from ..terminal.terminal import is_narrow
    from .ansi_utils import visual_width as _vw

    lines = text.split("\n")
    if width is None:
        width = max((_vw(line) for line in lines), default=0)
    width = max(width, 1)

    # 窄屏降级：使用呼吸前景色替代霓虹闪烁
    if is_narrow():
        color = sine_color(frame, base_color, min(255, base_color + 10), 12)
        color_ansi = f"\033[38;5;{color}m"
        reset = "\033[0m"
        h_line = "\u2500" * width
        top = f"{color_ansi}\u250c{h_line}\u2510{reset}"
        body = "\n".join(
            f"{color_ansi}\u2502{line}{' ' * (width - _vw(line))}\u2502{reset}"
            for line in lines
        )
        bottom = f"{color_ansi}\u2514{h_line}\u2518{reset}"
        return f"{top}\n{body}\n{bottom}"

    # 全功能霓虹边框：每帧颜色微调
    top_color = neon_color(frame, base_color, spread=3)
    mid_color = neon_color(frame + 2, base_color, spread=2)
    bottom_color = neon_color(frame + 4, base_color, spread=3)

    top_ansi = f"\033[38;5;{top_color}m"
    mid_ansi = f"\033[38;5;{mid_color}m"
    bottom_ansi = f"\033[38;5;{bottom_color}m"
    reset = "\033[0m"

    h_line = "\u2500" * width
    top = f"{top_ansi}\u250c{h_line}\u2510{reset}"
    body = "\n".join(
        f"{mid_ansi}\u2502{line}{' ' * (width - _vw(line))}\u2502{reset}"
        for line in lines
    )
    bottom = f"{bottom_ansi}\u2514{h_line}\u2518{reset}"
    return f"{top}\n{body}\n{bottom}"


# ═══════════════════════════════════════════════════════════
# 打字机光标闪烁效果（typewriter_cursor）
# ═══════════════════════════════════════════════════════════


def typewriter_cursor(frame: int, period: int = 2) -> str:
    """打字机光标闪烁 — 返回可见光标或空白，交替闪烁。

    偶帧返回 ``▌``（左半块），奇帧返回空格，
    模拟打字机/终端输入光标的闪烁效果。

    Args:
        frame: 当前帧号。
        period: 闪烁周期帧数（默认 2 帧：显示/隐藏各 1 帧）。

    Returns:
        ``"▌"`` 或 ``" "``。
    """
    return "\u258c" if (frame // (period // 2)) % 2 == 0 else " "


def build_typewriter_ansi(
    text: str,
    reveal_count: int,
    frame: int,
    style: str | None = None,
) -> str:
    """构建打字机效果 ANSI 字符串 — 已揭示文本 + 闪烁光标。

    逐字符揭示文本内容，末尾追加闪烁光标，
    模拟打字机逐字输出的视觉效果。

    格式::

        {styled revealed_text}{cursor_char}

    Args:
        text: 完整文本内容。
        reveal_count: 已揭示的字符数（0 到 len(text)）。
        frame: 当前帧号（控制光标闪烁）。
        style: 可选样式，``"dim"`` 表示未揭示部分灰色显示。

    Returns:
        带打字机效果的 ANSI 字符串。
    """
    revealed = text[:reveal_count]
    cursor = typewriter_cursor(frame)

    if style == "dim" and reveal_count < len(text):
        # 已揭示部分 + 闪烁光标 + 未揭示部分（灰色）
        hidden = text[reveal_count:]
        return f"{revealed}{cursor}\033[2m{hidden}\033[0m"
    else:
        return f"{revealed}{cursor}"

# ═══════════════════════════════════════════════════════════
# EffectRegistry 合成器与效果包装
# ═══════════════════════════════════════════════════════════


class EffectRegistry:
    """命名效果注册表 — 统一发现和组合动画效果。

    模式参考 BreathPalette / StyleSheet，但注册效果函数而非颜色。
    模块加载时自动注册所有预定义效果。

    每个注册项为 (effect_fn, metadata) 元组，
    其中 metadata 包含 description、params、category。

    效果函数签名统一为 effect_fn(frame: int, **params) -> list[int] | str。
    — 返回色号列表（供渐变/分隔线使用）或 ANSI 字符串（供直接输出）。

    线程安全：所有操作为只读字典访问 + 纯函数。
    """

    _registry: ClassVar[dict[str, tuple[Callable, dict]]] = {}

    @classmethod
    def register(cls, name: str, effect_fn: Callable, **metadata) -> None:
        cls._registry[name] = (effect_fn, metadata)

    @classmethod
    def get(cls, name: str) -> Callable | None:
        entry = cls._registry.get(name)
        if entry is not None:
            return entry[0]
        return None

    @classmethod
    def has(cls, name: str) -> bool:
        return name in cls._registry

    @classmethod
    def list(cls) -> list[tuple[str, dict]]:
        return [(name, meta) for name, (_, meta) in cls._registry.items()]

    @classmethod
    def compose(cls, names: list[str], frame: int, **kwargs) -> list[int]:
        results: list[list[int]] = []
        for name in names:
            fn = cls.get(name)
            if fn is None:
                raise ValueError(f"未注册的效果: {name}")
            result = fn(frame, **kwargs)
            if isinstance(result, list):
                results.append(result)
        if not results:
            return []
        min_len = min(len(r) for r in results)
        return [
            max(0, min(255, round(sum(r[i] for r in results) / len(results))))
            for i in range(min_len)
        ]

    @classmethod
    def clear(cls) -> None:
        cls._registry.clear()

    @classmethod
    def all_names(cls) -> list[str]:
        return list(cls._registry.keys())


def _register_default_effects() -> None:
    """注册默认效果到 EffectRegistry。模块加载时自动调用。"""
    from typing import Any

    def _rainbow_effect(frame: int, **kwargs: Any) -> list[int]:
        length = kwargs.get("length", 8)
        return [rainbow_color(frame, i, length) for i in range(length)]

    def _aurora_effect(frame: int, **kwargs: Any) -> list[int]:
        length = kwargs.get("length", 12)
        colors = kwargs.get("colors")
        return build_aurora_gradient(length, frame, colors)

    def _pulse_effect(frame: int, **kwargs: Any) -> list[int]:
        length = kwargs.get("length", 12)
        color_low = kwargs.get("color_low", 239)
        color_high = kwargs.get("color_high", 45)
        return [pulse_train(frame, i, length, color_low, color_high)
                for i in range(length)]

    def _wave_effect(frame: int, **kwargs: Any) -> list[int]:
        length = kwargs.get("length", 12)
        start = kwargs.get("start_color", 45)
        end = kwargs.get("end_color", 237)
        from .gradient import gradient_range
        colors = gradient_range(start, end, length)
        amplitude = kwargs.get("amplitude", 2.0)
        return apply_wave(colors, frame, amplitude=amplitude)

    def _shimmer_effect(frame: int, **kwargs: Any) -> list[int]:
        length = kwargs.get("length", 12)
        start = kwargs.get("start_color", 45)
        end = kwargs.get("end_color", 237)
        from .gradient import gradient_range
        colors = gradient_range(start, end, length)
        return shimmer_apply(colors, frame)

    def _heat_wave_effect(frame: int, **kwargs: Any) -> list[int]:
        length = kwargs.get("length", 12)
        start = kwargs.get("start_color", 45)
        end = kwargs.get("end_color", 237)
        amplitude = kwargs.get("amplitude", 5.0)
        from .gradient import gradient_range
        colors = gradient_range(start, end, length)
        return [apply_heat_wave(c, i, frame, amplitude) for i, c in enumerate(colors)]

    EffectRegistry.register("rainbow", _rainbow_effect,
                            description="彩虹渐变效果",
                            params={"length": "色号数量"},
                            category="gradient")
    EffectRegistry.register("aurora", _aurora_effect,
                            description="极光飘动效果",
                            params={"length": "色号数量", "colors": "可选基础调色板"},
                            category="gradient")
    EffectRegistry.register("pulse", _pulse_effect,
                            description="脉冲列车效果",
                            params={"length": "宽度", "color_low": "基础色号", "color_high": "脉冲色号"},
                            category="gradient")
    EffectRegistry.register("wave", _wave_effect,
                            description="波动渐变效果",
                            params={"length": "宽度", "start_color": "起始色号", "end_color": "结束色号", "amplitude": "波动幅度"},
                            category="gradient")
    EffectRegistry.register("shimmer", _shimmer_effect,
                            description="流光扫光效果",
                            params={"length": "宽度", "start_color": "起始色号", "end_color": "结束色号"},
                            category="gradient")
    EffectRegistry.register("heat_wave", _heat_wave_effect,
                            description="热浪扭曲效果",
                            params={"length": "宽度", "start_color": "起始色号", "end_color": "结束色号", "amplitude": "热浪幅度"},
                            category="gradient")
    EffectRegistry.register("sparkle", None,
                            description="闪烁高亮效果",
                            params={"base_color": "基准色号", "period": "闪烁周期"},
                            category="ansi")
    EffectRegistry.register("glow", None,
                            description="辉光呼吸效果",
                            params={"base_color": "基准色号", "period": "呼吸周期"},
                            category="ansi")

    def _neon_effect(frame: int, **kwargs: Any) -> list[int]:
        length = kwargs.get("length", 12)
        base_color = kwargs.get("base_color", 51)
        spread = kwargs.get("spread", 3)
        return [neon_color(frame + i, base_color, spread) for i in range(length)]

    EffectRegistry.register("neon", _neon_effect,
                            description="霓虹边框效果",
                            params={"length": "色号数量", "base_color": "霓虹基准色号", "spread": "摆动幅度"},
                            category="gradient")

    def _typewriter_effect(frame: int, **kwargs: Any) -> list[int]:
        length = kwargs.get("length", 8)
        base_color = kwargs.get("base_color", 45)
        period = kwargs.get("period", 2)
        visible = (frame // (period // 2)) % 2 == 0
        fill = base_color if visible else 237
        return [fill] * length

    EffectRegistry.register("typewriter", _typewriter_effect,
                            description="打字机光标闪烁效果",
                            params={"length": "色号数量", "base_color": "基准色号", "period": "闪烁周期"},
                            category="gradient")


_register_default_effects()


__all__ = [
    # 正弦波工具
    "sine_breath_t", "sine_color", "sine_color_range",
    # 弹入
    "bounce_easing", "bounce_frame_color",
    # FadeIn 缓动因子
    "fade_factor", "fade_color",
    # 缓动
    "sine_easing",
    # 波动
    "wave_offset", "apply_wave",
    # 闪烁
    "sparkle_brightness", "sparkle_color",
    # 流光
    "shimmer_position", "shimmer_apply",
    # ANSI 生成器
    "build_fade_in_ansi_enhanced",
    "build_wave_sep_ansi",
    "build_shimmer_sep_ansi",
    "build_glow_ansi",
    "build_fg_breath_ansi",
    "build_bg_breath_ansi",
    "build_breath_ansi",
    # 主题动效消费者
    "get_theme_effect_color",
    # 新增渲染效果（2026-07-15 框架整合）
    "rainbow_color", "build_rainbow_ansi",
    "pulse_train", "build_pulse_train_ansi",
    "matrix_rain_color", "build_matrix_rain_ansi",
    "heat_wave_offset", "apply_heat_wave", "build_heat_wave_ansi",
    "aurora_color", "build_aurora_gradient", "build_aurora_ansi",
    # 霓虹 + 打字机效果（2026-07-15）
    "neon_color", "build_neon_border_ansi",
    "typewriter_cursor", "build_typewriter_ansi",
    # 效果注册表
    "EffectRegistry",
]
