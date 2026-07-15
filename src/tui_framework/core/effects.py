"""动效原语模块 — 所有动画效果的统一重导出入口。

实际实现已按效果类别拆分为子模块：
  - _wave.py   — 正弦波/呼吸/波动效果（sine_*, bounce_*, wave_*）
  - _sparkle.py — 闪烁/脉冲/高亮/辉光效果（sparkle_*, shimmer_*, build_glow_ansi 等）
  - _train.py   — 列车/扫光/流动效果（rainbow_*, pulse_*, aurora_* 等）
  - _compose.py — EffectRegistry 合成器与效果包装（neon_*, typewriter_*, EffectRegistry）

本模块仅做重导出，保持向后兼容——所有旧导入路径 ``from .effects import X`` 继续有效。

设计原则：
  - 纯函数：输入帧号 → 输出值/ANSI字符串，无副作用
  - 可缓存：热点动效使用 @lru_cache 减少重复计算
  - 窄屏安全：所有 ANSI 生成函数检查窄屏条件
  - 无 I/O：不涉及终端写入，仅生成 ANSI 序列
"""

from __future__ import annotations

# ── _wave ──────────────────────────────────────────────────
from ._wave import (
    sine_breath_t,
    sine_color,
    sine_color_range,
    bounce_easing,
    bounce_frame_color,
    wave_offset,
    apply_wave,
    build_fade_in_ansi_enhanced,
    build_wave_sep_ansi,
)

# ── _sparkle ───────────────────────────────────────────────
from ._sparkle import (
    sparkle_brightness,
    sparkle_color,
    shimmer_position,
    shimmer_apply,
    build_shimmer_sep_ansi,
    build_glow_ansi,
    build_fg_breath_ansi,
    build_bg_breath_ansi,
    get_theme_effect_color,
)

# ── _train ─────────────────────────────────────────────────
from ._train import (
    rainbow_color,
    build_rainbow_ansi,
    pulse_train,
    build_pulse_train_ansi,
    matrix_rain_color,
    build_matrix_rain_ansi,
    heat_wave_offset,
    apply_heat_wave,
    build_heat_wave_ansi,
    aurora_color,
    build_aurora_gradient,
    build_aurora_ansi,
)

# ── _compose ───────────────────────────────────────────────
from ._compose import (
    sine_easing,
    neon_color,
    build_neon_border_ansi,
    typewriter_cursor,
    build_typewriter_ansi,
    EffectRegistry,
)


__all__ = [
    # _wave
    "sine_breath_t", "sine_color", "sine_color_range",
    "bounce_easing", "bounce_frame_color",
    "wave_offset", "apply_wave",
    "build_fade_in_ansi_enhanced",
    "build_wave_sep_ansi",
    # _sparkle
    "sparkle_brightness", "sparkle_color",
    "shimmer_position", "shimmer_apply",
    "build_shimmer_sep_ansi",
    "build_glow_ansi",
    "build_fg_breath_ansi",
    "build_bg_breath_ansi",
    "get_theme_effect_color",
    # _train
    "rainbow_color", "build_rainbow_ansi",
    "pulse_train", "build_pulse_train_ansi",
    "matrix_rain_color", "build_matrix_rain_ansi",
    "heat_wave_offset", "apply_heat_wave", "build_heat_wave_ansi",
    "aurora_color", "build_aurora_gradient", "build_aurora_ansi",
    # _compose
    "sine_easing",
    "neon_color", "build_neon_border_ansi",
    "typewriter_cursor", "build_typewriter_ansi",
    "EffectRegistry",
]
