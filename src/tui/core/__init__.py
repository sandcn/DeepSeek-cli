"""TUI 核心基础工具层（Layer 0）。

提供动画基础设施、动效原语、状态容器、TTL 缓存、时间格式化、
文本工具和系统监控等基础功能。
"""

from __future__ import annotations

# ── 动画基础设施 ──
from .animator import AnimatorContext, BreathPalette

# ── 动效原语 ──
from .effects import (
    sine_breath_t, sine_color, sine_color_range,
    bounce_easing, bounce_frame_color,
    sine_easing,
    wave_offset, apply_wave,
    sparkle_brightness, sparkle_color,
    shimmer_position, shimmer_apply,
    build_fade_in_ansi_enhanced,
    build_wave_sep_ansi,
    build_shimmer_sep_ansi,
    build_glow_ansi,
    build_fg_breath_ansi,
    build_bg_breath_ansi,
    get_theme_effect_color,
    # 新增渲染效果（2026-07-15）
    rainbow_color, build_rainbow_ansi,
    pulse_train, build_pulse_train_ansi,
    matrix_rain_color, build_matrix_rain_ansi,
    heat_wave_offset, apply_heat_wave, build_heat_wave_ansi,
    aurora_color, build_aurora_gradient, build_aurora_ansi,
    # 效果注册表
    EffectRegistry,
)

# ── 状态容器 ──
from .state import UISessionState, InputState, StreamingState, TUIStateTree

# ── TTL 缓存 ──
from .ttl_cache import TTLCache

# ── 时间格式化 ──
from .time_format import format_elapsed, format_speed

# ── 文本工具 ──
from .text_utils import (
    truncate, build_gradient_ansi, build_gradient_ansi_frame,
    build_fade_in_ansi, build_warning_pulse_ansi, make_sep_gradient,
    build_bounce_ansi, build_sep_wave, build_sep_shimmer,
    build_sparkle_ansi, build_glow_ansi, build_left_border_ansi,
    parse_theme_color, make_sep_gradient_enhanced,
)

# ── 颜色值对象 ──
from .color import Color256, RGB, GradientDescriptor

# ── 样式描述器 ──
from .style import Style, StyledText, StyleSheet

# ── 系统监控（私有类，按需导入） ──
# _SystemMonitor 以下划线开头，不自动导出

# ── 渐变工具 ──
from .gradient import hex_to_256, gradient_step, gradient_range

# ── 预定义调色板 ──
from .palettes import (
    GRADIENT_SUNSET, GRADIENT_OCEAN, GRADIENT_FOREST,
    GRADIENT_FIRE, GRADIENT_NEON,
    GRADIENT_AURORA, GRADIENT_CORAL, GRADIENT_MINT, GRADIENT_TWILIGHT,
    GRADIENT_SUNRISE, GRADIENT_PURPLE, GRADIENT_ICE,
    GRADIENT_SOFT, GRADIENT_EMERALD,
    GRADIENT_ROSE, GRADIENT_LAVA, GRADIENT_GLACIER,
    GRADIENT_SUNSET2, GRADIENT_NEON_GREEN, GRADIENT_NEON_PINK,
    GRADIENT_GOLD, GRADIENT_SKY, GRADIENT_MAGMA, GRADIENT_OCEAN_DEEP,
    BREATH_CYAN, BREATH_GREEN, BREATH_PURPLE, BREATH_GOLD, BREATH_ROSE,
)

# ── 主题系统 ──
from .theme import THEME, THEMES, set_theme, get_active_theme, list_themes, get_theme_names_with_desc

# ── ANSI 工具 ──
from .ansi_utils import strip_ansi, visual_width, truncate_ansi_visual, skip_ansi_sgr, truncate_ansi_sgr, truncate_ansi_line

# ── 费用计算 ──
from .cost import compute_round_cost_data

# ── 输出目标 ──
from .output_target import IOutputTarget, TerminalTarget, BufferTarget, NullTarget

__all__ = [
    # animator
    "AnimatorContext", "BreathPalette",
    # effects
    "sine_breath_t", "sine_color", "sine_color_range",
    "bounce_easing", "bounce_frame_color",
    "wave_offset", "apply_wave",
    "sparkle_brightness", "sparkle_color",
    "shimmer_position", "shimmer_apply",
    "build_fade_in_ansi_enhanced",
    "build_wave_sep_ansi",
    "build_shimmer_sep_ansi",
    "build_glow_ansi",
    "build_fg_breath_ansi",
    "build_bg_breath_ansi",
    "get_theme_effect_color",
    # 新增渲染效果（2026-07-15）
    "rainbow_color", "build_rainbow_ansi",
    "pulse_train", "build_pulse_train_ansi",
    "matrix_rain_color", "build_matrix_rain_ansi",
    "heat_wave_offset", "apply_heat_wave", "build_heat_wave_ansi",
    "aurora_color", "build_aurora_gradient", "build_aurora_ansi",
    # 缓动（统一入口，2026-07-15）
    "sine_easing",
    # 效果注册表（2026-07-15）
    "EffectRegistry",
    # state
    "UISessionState", "InputState", "StreamingState", "TUIStateTree",
    # ttl_cache
    "TTLCache",
    # time_format
    "format_elapsed", "format_speed",
    # text_utils
    "truncate", "build_gradient_ansi", "build_gradient_ansi_frame",
    "build_fade_in_ansi", "build_warning_pulse_ansi", "make_sep_gradient",
    "build_bounce_ansi", "build_sep_wave", "build_sep_shimmer",
    "build_sparkle_ansi", "build_glow_ansi", "build_left_border_ansi",
    "parse_theme_color", "make_sep_gradient_enhanced",
    # color
    "Color256", "RGB", "GradientDescriptor",
    # style
    "Style", "StyledText", "StyleSheet",
    # gradient
    "hex_to_256", "gradient_step", "gradient_range",
    # palettes
    "GRADIENT_SUNSET", "GRADIENT_OCEAN", "GRADIENT_FOREST",
    "GRADIENT_FIRE", "GRADIENT_NEON",
    "GRADIENT_AURORA", "GRADIENT_CORAL", "GRADIENT_MINT", "GRADIENT_TWILIGHT",
    "GRADIENT_SUNRISE", "GRADIENT_PURPLE", "GRADIENT_ICE",
    "GRADIENT_SOFT", "GRADIENT_EMERALD",
    "GRADIENT_ROSE", "GRADIENT_LAVA", "GRADIENT_GLACIER",
    "GRADIENT_SUNSET2", "GRADIENT_NEON_GREEN", "GRADIENT_NEON_PINK",
    "GRADIENT_GOLD", "GRADIENT_SKY", "GRADIENT_MAGMA", "GRADIENT_OCEAN_DEEP",
    "BREATH_CYAN", "BREATH_GREEN", "BREATH_PURPLE", "BREATH_GOLD", "BREATH_ROSE",
    # theme
    "THEME", "THEMES", "set_theme", "get_active_theme", "list_themes", "get_theme_names_with_desc",
    # ansi_utils
    "strip_ansi", "visual_width", "truncate_ansi_visual", "skip_ansi_sgr", "truncate_ansi_sgr", "truncate_ansi_line",
    # output_target
    "IOutputTarget", "TerminalTarget", "BufferTarget", "NullTarget",
    # cost
    "compute_round_cost_data",
]
