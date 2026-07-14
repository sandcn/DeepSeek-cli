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

# ── 系统监控（私有类，按需导入） ──
# _SystemMonitor 以下划线开头，不自动导出

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
]
