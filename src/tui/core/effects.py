"""动效原语模块 — 所有动画效果的纯函数实现。

集中管理所有动画动效的计算逻辑，消除散落在各显示组件中
的重复实现。所有函数为纯计算，不依赖 AnimatorContext/
BreathPalette，直接接受帧号作为参数，可独立测试。

设计原则：
  - 纯函数：输入帧号 → 输出值/ANSI字符串，无副作用
  - 可缓存：热点动效使用 @lru_cache 减少重复计算
  - 窄屏安全：所有 ANSI 生成函数检查窄屏条件
  - 无 I/O：不涉及终端写入，仅生成 ANSI 序列

动效类型：
  - sine_*：基于正弦波的平滑呼吸动效
  - bounce_*：带弹跳超调的弹入动效
  - wave_*：波动渐变效果
  - sparkle_*：闪烁高亮效果
  - shimmer_*：流光扫光效果
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
    import re
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


# ═══════════════════════════════════════════════════════════
# 新增渲染效果（2026-07-15 框架整合）
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
    # 正弦波工具
    "sine_breath_t", "sine_color", "sine_color_range",
    # 弹入
    "bounce_easing", "bounce_frame_color",
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
    # 主题动效消费者
    "get_theme_effect_color",
    # 新增渲染效果（2026-07-15 框架整合）
    "rainbow_color", "build_rainbow_ansi",
    "pulse_train", "build_pulse_train_ansi",
    "matrix_rain_color", "build_matrix_rain_ansi",
    "heat_wave_offset", "apply_heat_wave", "build_heat_wave_ansi",
    "aurora_color", "build_aurora_gradient", "build_aurora_ansi",
]
