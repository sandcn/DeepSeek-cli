"""文本工具函数 — 委托到 tui_framework."""
from tui_framework.core.text_utils import *

__all__ = [
    "truncate", "build_gradient_ansi", "build_gradient_ansi_frame",
    "build_fade_in_ansi", "build_warning_pulse_ansi", "make_sep_gradient",
    # 增强动效（2026-07-12）
    "build_bounce_ansi", "build_sep_wave", "build_sep_shimmer",
    "build_sparkle_ansi", "build_glow_ansi", "build_left_border_ansi",
    "parse_theme_color",
    "make_sep_gradient_enhanced",
]
