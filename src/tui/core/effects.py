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

from ._wave import (
    apply_wave,
    bounce_easing,
    bounce_frame_color,
    build_bg_breath_ansi,
    build_breath_ansi,
    build_fade_in_ansi_enhanced,
    build_fg_breath_ansi,
    build_glow_ansi,
    build_wave_sep_ansi,
    fade_color,
    fade_factor,
    get_theme_effect_color,
    sine_breath_t,
    sine_color,
    sine_color_range,
    sine_easing,
    wave_offset,
)

from ._sparkle import (
    sparkle_brightness,
    sparkle_color,
)

from ._train import (
    apply_heat_wave,
    aurora_color,
    build_aurora_ansi,
    build_aurora_gradient,
    build_heat_wave_ansi,
    build_matrix_rain_ansi,
    build_neon_border_ansi,
    build_pulse_train_ansi,
    build_rainbow_ansi,
    build_shimmer_sep_ansi,
    build_typewriter_ansi,
    heat_wave_offset,
    matrix_rain_color,
    neon_color,
    pulse_train,
    rainbow_color,
    shimmer_apply,
    shimmer_position,
    typewriter_cursor,
)

from ._compose import EffectRegistry


# ═══════════════════════════════════════════════════════════
# 向后兼容别名（已从 text_utils.py 删除的函数）
# ═══════════════════════════════════════════════════════════

build_sep_wave = build_wave_sep_ansi
build_sep_shimmer = build_shimmer_sep_ansi


def build_sparkle_ansi(frame: int, base_color: int = 45, period: int = 6) -> str:
    """构建闪烁 ANSI 序列（向后兼容包装器）。

    原位于 text_utils.py，已删除。现基于 sparkle_color 实现。

    Args:
        frame: 当前帧号。
        base_color: 基准色号，默认 45（青色）。
        period: 闪烁周期帧数。

    Returns:
        ANSI 前景色序列。
    """
    c = sparkle_color(frame, base_color, period=period)
    return f"\033[38;5;{c}m"


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
    "build_sep_wave",
    "build_sep_shimmer",
    "build_sparkle_ansi",
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
