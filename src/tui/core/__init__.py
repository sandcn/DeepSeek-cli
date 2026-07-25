"""TUI 核心基础工具层（Layer 0）。

提供颜色、样式、ANSI 工具、文本格式化、动画效果、调色板、主题等基础功能。
子模块直接导入即可，本 __init__.py 仅保留 __all__ 作为公开 API 清单。
"""

from __future__ import annotations


__all__ = [
    # animator
    "AnimatorContext", "BreathPalette",
    # effects
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
    "rainbow_color", "build_rainbow_ansi",
    "pulse_train", "build_pulse_train_ansi",
    "matrix_rain_color", "build_matrix_rain_ansi",
    "heat_wave_offset", "apply_heat_wave", "build_heat_wave_ansi",
    "aurora_color", "build_aurora_gradient", "build_aurora_ansi",
    "neon_color", "build_neon_border_ansi",
    "typewriter_cursor", "build_typewriter_ansi",
    "sine_easing",
    "EffectRegistry",
    # ttl_cache
    "TTLCache",
    # formatter
    "format_elapsed", "format_speed",
    "format_all_params", "extract_key_params",
    # text_utils
    "truncate", "build_gradient_ansi", "build_gradient_ansi_frame",
    "build_fade_in_ansi", "build_warning_pulse_ansi", "make_sep_gradient",
    "build_bounce_ansi", "build_sep_wave", "build_sep_shimmer",
    "build_sparkle_ansi",
    "parse_theme_color",
    # color
    "Color256", "TrueColor", "GradientDescriptor",
    # style
    "Style", "StyleSheet",
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
    "THEME", "THEMES", "set_theme", "get_active_theme", "list_themes",
    "get_theme_names_with_desc",
    "load_user_themes", "load_user_themes_into_themes", "reload_themes",
    "load_user_themes_from_dir", "parse_simple_yaml",
    # ansi_utils
    "strip_ansi", "visual_width", "truncate_ansi_visual", "skip_ansi_sgr",
    "truncate_ansi_sgr", "truncate_ansi_line",
    # output_target
    "IOutputTarget", "TerminalTarget", "BufferTarget", "NullTarget",
    "get_console",
    # cost
    "compute_round_cost_data",
]
