"""TUI 核心基础工具层（Layer 0）。

提供动画基础设施、动效原语、状态容器、TTL 缓存、时间格式化、
文本工具和系统监控等基础功能。

模块采用懒加载模式（LazyLoader），首次属性访问时才执行实际 import，
降低应用启动时的模块加载开销。

新增模块（2026-07-15 框架增强）:
  - parallel_config: 并行显示常量与自适应配置（从 parallel 下沉）
  - tool_icons: 工具颜色和图标主题定义（从 parallel 下沉）
  - formatter: 文本格式化工具函数
  - TrueColor / ColorValue: 24-bit 真彩色值对象与联合类型（颜色体系扩展）
  - theme_loader: 轻量级 YAML 用户主题加载器
"""

from __future__ import annotations

from .._lazy import LazyLoader


# ═══════════════════════════════════════════════════════════
# 懒加载模块代理
# ═══════════════════════════════════════════════════════════

_animator = LazyLoader("src.tui.animation.animator")
_effects_mod = LazyLoader("src.tui.core.effects")
_state_session = LazyLoader("src.tui.state.session_state")
_state_input = LazyLoader("src.tui.state.input_state")
_state_streaming = LazyLoader("src.tui.state.streaming_state")
_state_tree = LazyLoader("src.tui.state.tui_state_tree")
_ttl_cache_mod = LazyLoader("src.tui.core.ttl_cache")
_formatter_mod = LazyLoader("src.tui.core.formatter")
_text_utils_mod = LazyLoader("src.tui.core.text_utils")
_color_mod = LazyLoader("src.tui.core.color")
_style_mod = LazyLoader("src.tui.core.style")
_gradient_mod = LazyLoader("src.tui.core.gradient")
_palettes_mod = LazyLoader("src.tui.animation.palettes")
_theme_mod = LazyLoader("src.tui.core.theme")
_theme_loader_mod = LazyLoader("src.tui.core.theme_loader")
_ansi_utils_mod = LazyLoader("src.tui.core.ansi_utils")
_cost_mod = LazyLoader("src.tui.core.cost")
_rich_console_mod = LazyLoader("src.tui.core.rich_console")
_output_target_mod = LazyLoader("src.tui.core.output_target")


# ═══════════════════════════════════════════════════════════
# 符号到懒加载模块的映射（供 __getattr__ 使用）
# ═══════════════════════════════════════════════════════════

_SYMBOL_MAP: dict[str, LazyLoader] = {
    # animator
    "AnimatorContext": _animator,
    "BreathPalette": _animator,
    # effects
    "sine_breath_t": _effects_mod,
    "sine_color": _effects_mod,
    "sine_color_range": _effects_mod,
    "bounce_easing": _effects_mod,
    "bounce_frame_color": _effects_mod,
    "sine_easing": _effects_mod,
    "wave_offset": _effects_mod,
    "apply_wave": _effects_mod,
    "sparkle_brightness": _effects_mod,
    "sparkle_color": _effects_mod,
    "shimmer_position": _effects_mod,
    "shimmer_apply": _effects_mod,
    "build_fade_in_ansi_enhanced": _effects_mod,
    "build_wave_sep_ansi": _effects_mod,
    "build_shimmer_sep_ansi": _effects_mod,
    "build_glow_ansi": _effects_mod,
    "build_fg_breath_ansi": _effects_mod,
    "build_bg_breath_ansi": _effects_mod,
    "get_theme_effect_color": _effects_mod,
    "rainbow_color": _effects_mod,
    "build_rainbow_ansi": _effects_mod,
    "pulse_train": _effects_mod,
    "build_pulse_train_ansi": _effects_mod,
    "matrix_rain_color": _effects_mod,
    "build_matrix_rain_ansi": _effects_mod,
    "heat_wave_offset": _effects_mod,
    "apply_heat_wave": _effects_mod,
    "build_heat_wave_ansi": _effects_mod,
    "aurora_color": _effects_mod,
    "build_aurora_gradient": _effects_mod,
    "build_aurora_ansi": _effects_mod,
    "neon_color": _effects_mod,
    "build_neon_border_ansi": _effects_mod,
    "typewriter_cursor": _effects_mod,
    "build_typewriter_ansi": _effects_mod,
    "EffectRegistry": _effects_mod,
    # state
    "UISessionState": _state_session,
    "InputState": _state_input,
    "StreamingState": _state_streaming,
    "TUIStateTree": _state_tree,
    # ttl_cache
    "TTLCache": _ttl_cache_mod,
    # formatter
    "format_elapsed": _formatter_mod,
    "format_speed": _formatter_mod,
    # text_utils
    "truncate": _text_utils_mod,
    "build_gradient_ansi": _text_utils_mod,
    "build_gradient_ansi_frame": _text_utils_mod,
    "build_fade_in_ansi": _text_utils_mod,
    "build_warning_pulse_ansi": _text_utils_mod,
    "make_sep_gradient": _text_utils_mod,
    "build_bounce_ansi": _text_utils_mod,
    "build_sep_wave": _text_utils_mod,
    "build_sep_shimmer": _text_utils_mod,
    "build_sparkle_ansi": _text_utils_mod,
    "build_left_border_ansi": _text_utils_mod,
    "parse_theme_color": _text_utils_mod,
    "make_sep_gradient_enhanced": _text_utils_mod,
    # color
    "Color256": _color_mod,
    "RGB": _color_mod,
    "TrueColor": _color_mod,
    "GradientDescriptor": _color_mod,
    "ColorValue": _color_mod,
    "to_ansi_fg": _color_mod,
    "to_ansi_bg": _color_mod,
    "to_256": _color_mod,
    "auto_color": _color_mod,
    # style
    "Style": _style_mod,
    "StyledText": _style_mod,
    "StyleSheet": _style_mod,
    # gradient
    "hex_to_256": _gradient_mod,
    "gradient_step": _gradient_mod,
    "gradient_range": _gradient_mod,
    # palettes
    "GRADIENT_SUNSET": _palettes_mod,
    "GRADIENT_OCEAN": _palettes_mod,
    "GRADIENT_FOREST": _palettes_mod,
    "GRADIENT_FIRE": _palettes_mod,
    "GRADIENT_NEON": _palettes_mod,
    "GRADIENT_AURORA": _palettes_mod,
    "GRADIENT_CORAL": _palettes_mod,
    "GRADIENT_MINT": _palettes_mod,
    "GRADIENT_TWILIGHT": _palettes_mod,
    "GRADIENT_SUNRISE": _palettes_mod,
    "GRADIENT_PURPLE": _palettes_mod,
    "GRADIENT_ICE": _palettes_mod,
    "GRADIENT_SOFT": _palettes_mod,
    "GRADIENT_EMERALD": _palettes_mod,
    "GRADIENT_ROSE": _palettes_mod,
    "GRADIENT_LAVA": _palettes_mod,
    "GRADIENT_GLACIER": _palettes_mod,
    "GRADIENT_SUNSET2": _palettes_mod,
    "GRADIENT_NEON_GREEN": _palettes_mod,
    "GRADIENT_NEON_PINK": _palettes_mod,
    "GRADIENT_GOLD": _palettes_mod,
    "GRADIENT_SKY": _palettes_mod,
    "GRADIENT_MAGMA": _palettes_mod,
    "GRADIENT_OCEAN_DEEP": _palettes_mod,
    "BREATH_CYAN": _palettes_mod,
    "BREATH_GREEN": _palettes_mod,
    "BREATH_PURPLE": _palettes_mod,
    "BREATH_GOLD": _palettes_mod,
    "BREATH_ROSE": _palettes_mod,
    # theme
    "THEME": _theme_mod,
    "THEMES": _theme_mod,
    "set_theme": _theme_mod,
    "get_active_theme": _theme_mod,
    "list_themes": _theme_mod,
    "get_theme_names_with_desc": _theme_mod,
    "load_user_themes": _theme_mod,
    "load_user_themes_into_themes": _theme_mod,
    "reload_themes": _theme_mod,
    # theme_loader
    "load_user_themes_from_dir": _theme_loader_mod,
    "parse_simple_yaml": _theme_loader_mod,
    # ansi_utils
    "strip_ansi": _ansi_utils_mod,
    "visual_width": _ansi_utils_mod,
    "truncate_ansi_visual": _ansi_utils_mod,
    "skip_ansi_sgr": _ansi_utils_mod,
    "truncate_ansi_sgr": _ansi_utils_mod,
    "truncate_ansi_line": _ansi_utils_mod,
    # cost
    "compute_round_cost_data": _cost_mod,
    # rich_console
    "get_console": _rich_console_mod,
    # output_target
    "IOutputTarget": _output_target_mod,
    "TerminalTarget": _output_target_mod,
    "BufferTarget": _output_target_mod,
    "NullTarget": _output_target_mod,
}


def __getattr__(name: str):
    """模块级 __getattr__ — 从对应懒加载模块延迟解析符号。

    当 ``from src.tui.core import XXX`` 执行时，如果 XXX 不是模块的
    直接属性，Python 会调用此函数，从 _SYMBOL_MAP 中查找对应的
    LazyLoader 并执行延迟导入。

    Raises:
        AttributeError: 符号不在 __all__ 中时抛出。
    """
    loader = _SYMBOL_MAP.get(name)
    if loader is not None:
        return getattr(loader, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """支持 dir() 列出所有导出符号。"""
    return sorted(__all__)


# ═══════════════════════════════════════════════════════════
# __all__ — 公开 API 清单
# ═══════════════════════════════════════════════════════════

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
    # 霓虹 + 打字机效果（2026-07-15 步骤7）
    "neon_color", "build_neon_border_ansi",
    "typewriter_cursor", "build_typewriter_ansi",
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
    "build_sparkle_ansi", "build_left_border_ansi",
    "parse_theme_color", "make_sep_gradient_enhanced",
    # color
    "Color256", "RGB", "TrueColor", "GradientDescriptor",
    "ColorValue", "to_ansi_fg", "to_ansi_bg", "to_256", "auto_color",
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
    "load_user_themes", "load_user_themes_into_themes", "reload_themes",
    # theme_loader
    "load_user_themes_from_dir", "parse_simple_yaml",
    # ansi_utils
    "strip_ansi", "visual_width", "truncate_ansi_visual", "skip_ansi_sgr", "truncate_ansi_sgr", "truncate_ansi_line",
    # output_target
    "IOutputTarget", "TerminalTarget", "BufferTarget", "NullTarget",
    # rich_console
    "get_console",
    # cost
    "compute_round_cost_data",
]
