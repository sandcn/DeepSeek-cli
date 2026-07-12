"""
语义化主题颜色映射 — 支持多主题切换。

每个主题预设为 {语义键: ANSI颜色码} 映射。
通过 set_theme(name) 动态切换，THEME 始终保持为当前活动主题的引用。

所有颜色值已升级为 256 色 ANSI 码（格式 \033[38;5;Nm 或 \033[48;5;Nm），
保持与 src/core/constants.py 中 _256 后缀常量一致。
"""
from __future__ import annotations

from typing import Dict, List

# ══════════════════════════════════════════════════════════
# 主题预设
# ══════════════════════════════════════════════════════════

THEMES: Dict[str, Dict[str, str]] = {
    # ── 深色主题（默认）────────────────────────────────
    "dark": {
        "title": "\033[38;5;45m",          # 青色
        "subtitle": "\033[38;5;242m",      # 中灰
        "prompt": "\033[38;5;45m",         # 青色
        "user": "\033[38;5;45m",           # 青色
        "assistant": "\033[38;5;41m",      # 绿色
        "thinking": "\033[38;5;242m",      # 中灰
        "tool": "\033[38;5;242m",          # 中灰
        "success": "\033[38;5;41m",        # 绿色
        "warning": "\033[38;5;221m",       # 琥珀黄
        "error": "\033[38;5;196m",         # 红色
        "info": "\033[38;5;242m",          # 中灰
        "cost": "\033[38;5;242m",          # 中灰
        "separator": "\033[38;5;239m",     # 暗灰
        "meta": "\033[38;5;242m",          # 中灰
        "accent": "\033[38;5;221m",        # 琥珀黄
        "border": "\033[38;5;239m",        # 暗灰
        "highlight": "\033[38;5;45m",      # 青色
        "muted": "\033[38;5;237m",         # 深灰
        "code": "\033[38;5;242m",          # 中灰
        "divider": "\033[38;5;239m",       # 暗灰
        # ── 新增语义键（步骤 3） ──
        "progress_filled": "\033[38;5;41m",   # 绿色
        "progress_empty": "\033[38;5;236m",   # 深灰
        "diff_add": "\033[38;5;41m",          # 绿色
        "diff_del": "\033[38;5;196m",         # 红色
        "diff_ctx": "\033[38;5;242m",         # 中灰
        "border_active": "\033[38;5;45m",     # 青色
        "border_inactive": "\033[38;5;237m",  # 深灰
        "overlay_bg": "\033[48;5;235m",       # 暗色背景
        "tag_code": "\033[38;5;221m",         # 琥珀黄
        "prompt_glow": "\033[38;5;45m",       # 青色 — 提示符发光
        "border_glow": "\033[38;5;40m",       # 中青 — 边框发光
        "status_pulse": "\033[38;5;214m",     # 琥珀 — 状态脉动
        "breathing_base": "\033[38;5;32m",    # 暗青 — 呼吸基准
        "pulse_highlight": "\033[38;5;81m",   # 亮青 — 脉冲高亮
        "separator_glow": "\033[38;5;45m",    # 青色 — 分隔线发光
        "tag_glow": "\033[38;5;45m",          # 青色 — 标签发光
        # ── 动效语义键（Phase 5） ──
        "effect_bounce": "\033[38;5;45m",     # 弹入动效主题色（青色）
        "effect_wave": "\033[38;5;44m",       # 波动动效主题色（中青）
        "effect_sparkle": "\033[38;5;81m",    # 闪烁高亮色（亮青）
        "effect_glow": "\033[38;5;221m",      # 辉光色（琥珀黄）
        "effect_shimmer": "\033[38;5;195m",   # 流光色（亮白青）
        # ── 新增美化语义键（2026-07-12 TUI 美化） ──
        "user_glow": "\033[38;5;81m",         # 亮青 — 用户消息前缀闪烁
        "placeholder_glow": "\033[38;5;45m",  # 青色 — 底部栏占位符辉光
        "border_breath": "\033[38;5;23m",     # 暗青 — 呼吸边框基准
        "model_breath": "\033[38;5;45m",      # 青色 — 模型名呼吸色
        "tag_breath": "\033[38;5;45m",        # 青色 — 角色标签呼吸色
        "effect_border": "\033[38;5;40m",     # 中青 — 边框动效色
        "deco_glow": "\033[38;5;221m",        # 琥珀黄 — 装饰辉光
        "icon_glow": "\033[38;5;81m",         # 亮青 — 图标辉光
        "accent_pulse": "\033[38;5;214m",     # 琥珀 — 强调脉动
    },

    # ── 亮色主题（浅色背景用）──────────────────────────
    "light": {
        "title": "\033[38;5;33m",          # 蓝色
        "subtitle": "\033[38;5;242m",      # 中灰
        "prompt": "\033[38;5;33m",         # 蓝色
        "user": "\033[38;5;33m",           # 蓝色
        "assistant": "\033[38;5;41m",      # 绿色
        "thinking": "\033[38;5;242m",      # 中灰
        "tool": "\033[38;5;242m",          # 中灰
        "success": "\033[38;5;41m",        # 绿色
        "warning": "\033[1;38;5;221m",     # BOLD + 琥珀黄
        "error": "\033[38;5;196m",         # 红色
        "info": "\033[38;5;242m",          # 中灰
        "cost": "\033[38;5;242m",          # 中灰
        "separator": "\033[38;5;239m",     # 暗灰
        "meta": "\033[38;5;242m",          # 中灰
        "accent": "\033[1;38;5;221m",      # BOLD + 琥珀黄
        "border": "\033[38;5;239m",        # 暗灰
        "highlight": "\033[38;5;33m",      # 蓝色
        "muted": "\033[38;5;242m",         # 中灰
        "code": "\033[38;5;242m",          # 中灰
        "divider": "\033[38;5;239m",       # 暗灰
        # ── 新增语义键（步骤 3） ──
        "progress_filled": "\033[38;5;41m",
        "progress_empty": "\033[38;5;236m",
        "diff_add": "\033[38;5;41m",
        "diff_del": "\033[38;5;196m",
        "diff_ctx": "\033[38;5;242m",
        "border_active": "\033[38;5;33m",
        "border_inactive": "\033[38;5;237m",
        "overlay_bg": "\033[48;5;235m",
        "tag_code": "\033[38;5;221m",
        "prompt_glow": "\033[38;5;33m",       # 蓝色
        "border_glow": "\033[38;5;32m",       # 中蓝
        "status_pulse": "\033[38;5;220m",     # 黄
        "breathing_base": "\033[38;5;26m",    # 暗蓝
        "pulse_highlight": "\033[38;5;75m",   # 亮蓝
        "separator_glow": "\033[38;5;33m",    # 蓝色
        "tag_glow": "\033[38;5;33m",          # 蓝色
        # ── 动效语义键（Phase 5） ──
        "effect_bounce": "\033[38;5;33m",     # 蓝色
        "effect_wave": "\033[38;5;32m",       # 中蓝
        "effect_sparkle": "\033[38;5;75m",    # 亮蓝
        "effect_glow": "\033[38;5;220m",      # 金色
        "effect_shimmer": "\033[38;5;117m",   # 亮天蓝
        # ── 新增美化语义键（2026-07-12 TUI 美化） ──
        "user_glow": "\033[38;5;75m",         # 亮蓝 — 用户消息前缀闪烁
        "placeholder_glow": "\033[38;5;33m",  # 蓝色 — 底部栏占位符辉光
        "border_breath": "\033[38;5;25m",     # 暗蓝 — 呼吸边框基准
        "model_breath": "\033[38;5;33m",      # 蓝色 — 模型名呼吸色
        "tag_breath": "\033[38;5;33m",        # 蓝色 — 角色标签呼吸色
        "effect_border": "\033[38;5;32m",     # 中蓝 — 边框动效色
        "deco_glow": "\033[38;5;220m",        # 金色 — 装饰辉光
        "icon_glow": "\033[38;5;75m",         # 亮蓝 — 图标辉光
        "accent_pulse": "\033[38;5;214m",     # 琥珀 — 强调脉动
    },

    # ── 高对比主题（高可读性）──────────────────────────
    "high-contrast": {
        "title": "\033[38;5;81m",          # 亮青
        "subtitle": "\033[38;5;242m",      # 中灰
        "prompt": "\033[38;5;81m",         # 亮青
        "user": "\033[38;5;81m",           # 亮青
        "assistant": "\033[38;5;47m",      # 亮绿
        "thinking": "\033[38;5;15m",       # 白
        "tool": "\033[38;5;15m",           # 白
        "success": "\033[38;5;47m",        # 亮绿
        "warning": "\033[38;5;227m",       # 亮黄
        "error": "\033[1;38;5;196m",       # BOLD + 红
        "info": "\033[38;5;255m",          # 亮白
        "cost": "\033[38;5;255m",          # 亮白
        "separator": "\033[38;5;242m",     # 中灰
        "meta": "\033[38;5;255m",          # 亮白
        "accent": "\033[38;5;227m",        # 亮黄
        "border": "\033[38;5;255m",        # 亮白
        "highlight": "\033[38;5;81m",      # 亮青
        "muted": "\033[38;5;250m",         # 浅灰
        "code": "\033[38;5;255m",          # 亮白
        "divider": "\033[38;5;242m",       # 中灰
        # ── 新增语义键（步骤 3） ──
        "progress_filled": "\033[38;5;47m",
        "progress_empty": "\033[38;5;236m",
        "diff_add": "\033[38;5;47m",
        "diff_del": "\033[1;38;5;196m",
        "diff_ctx": "\033[38;5;255m",
        "border_active": "\033[38;5;81m",
        "border_inactive": "\033[38;5;242m",
        "overlay_bg": "\033[48;5;235m",
        "tag_code": "\033[38;5;227m",
        "prompt_glow": "\033[38;5;81m",       # 亮青
        "border_glow": "\033[38;5;81m",       # 亮青
        "status_pulse": "\033[38;5;227m",     # 亮黄
        "breathing_base": "\033[38;5;32m",    # 暗青
        "pulse_highlight": "\033[38;5;81m",   # 亮青
        "separator_glow": "\033[38;5;81m",    # 亮青
        "tag_glow": "\033[38;5;81m",          # 亮青
        # ── 动效语义键（Phase 5） ──
        "effect_bounce": "\033[38;5;81m",     # 亮青
        "effect_wave": "\033[38;5;75m",       # 中亮蓝
        "effect_sparkle": "\033[38;5;51m",    # 最亮青
        "effect_glow": "\033[38;5;227m",      # 亮黄
        "effect_shimmer": "\033[38;5;255m",   # 亮白
        # ── 新增美化语义键（2026-07-12 TUI 美化） ──
        "user_glow": "\033[38;5;81m",         # 亮青 — 用户消息前缀闪烁
        "placeholder_glow": "\033[38;5;81m",  # 亮青 — 底部栏占位符辉光
        "border_breath": "\033[38;5;33m",     # 中蓝 — 呼吸边框基准
        "model_breath": "\033[38;5;81m",      # 亮青 — 模型名呼吸色
        "tag_breath": "\033[38;5;81m",        # 亮青 — 角色标签呼吸色
        "effect_border": "\033[38;5;51m",     # 最亮青 — 边框动效色
        "deco_glow": "\033[38;5;227m",        # 亮黄 — 装饰辉光
        "icon_glow": "\033[38;5;81m",         # 亮青 — 图标辉光
        "accent_pulse": "\033[38;5;227m",     # 亮黄 — 强调脉动
    },

    # ── Nord 极光主题（蓝灰冰霜风）────────────────────
    "nord": {
        # ── 基础语义键（Nord 极夜 + 冰霜配色） ──
        "title": "\033[38;5;67m",          # 冰蓝 #5E81AC
        "subtitle": "\033[38;5;242m",      # 中灰
        "prompt": "\033[38;5;109m",        # 冰蓝绿 #8FBCBB
        "user": "\033[38;5;109m",          # 冰蓝绿 #8FBCBB
        "assistant": "\033[38;5;108m",     # 极光绿 #A3BE8C
        "thinking": "\033[38;5;242m",      # 中灰
        "tool": "\033[38;5;242m",          # 中灰
        "success": "\033[38;5;108m",       # 极光绿 #A3BE8C
        "warning": "\033[38;5;221m",       # 极光黄 #EBCB8B
        "error": "\033[38;5;167m",         # 极光红 #BF616A
        "info": "\033[38;5;242m",          # 中灰
        "cost": "\033[38;5;242m",          # 中灰
        "separator": "\033[38;5;239m",     # 极夜灰 #4C566A
        "meta": "\033[38;5;242m",          # 中灰
        "accent": "\033[38;5;221m",        # 极光黄 #EBCB8B
        "border": "\033[38;5;239m",        # 极夜灰 #4C566A
        "highlight": "\033[38;5;109m",     # 冰蓝绿 #8FBCBB
        "muted": "\033[38;5;237m",         # 极夜灰 #434C5E
        "code": "\033[38;5;242m",          # 中灰
        "divider": "\033[38;5;239m",       # 极夜灰 #4C566A
        # ── progress / diff / border / overlay ──
        "progress_filled": "\033[38;5;108m",
        "progress_empty": "\033[38;5;236m",
        "diff_add": "\033[38;5;108m",
        "diff_del": "\033[38;5;167m",
        "diff_ctx": "\033[38;5;242m",
        "border_active": "\033[38;5;109m",
        "border_inactive": "\033[38;5;237m",
        "overlay_bg": "\033[48;5;235m",
        "tag_code": "\033[38;5;221m",
        # ── glow / pulse 发光语义 ──
        "prompt_glow": "\033[38;5;109m",      # 冰蓝绿
        "border_glow": "\033[38;5;67m",       # 冰蓝
        "status_pulse": "\033[38;5;221m",     # 极光黄
        "breathing_base": "\033[38;5;32m",    # 暗青
        "pulse_highlight": "\033[38;5;81m",   # 亮青
        "separator_glow": "\033[38;5;67m",    # 冰蓝
        "tag_glow": "\033[38;5;109m",         # 冰蓝绿
        # ── 动效语义键 ──
        "effect_bounce": "\033[38;5;109m",    # 冰蓝绿
        "effect_wave": "\033[38;5;67m",       # 冰蓝
        "effect_sparkle": "\033[38;5;81m",    # 亮青
        "effect_glow": "\033[38;5;221m",      # 极光黄
        "effect_shimmer": "\033[38;5;195m",   # 亮白青
        # ── 新增美化语义键 ──
        "user_glow": "\033[38;5;81m",         # 亮青
        "placeholder_glow": "\033[38;5;109m", # 冰蓝绿
        "border_breath": "\033[38;5;24m",     # 暗蓝青
        "model_breath": "\033[38;5;67m",      # 冰蓝
        "tag_breath": "\033[38;5;109m",       # 冰蓝绿
        "effect_border": "\033[38;5;67m",     # 冰蓝
        "deco_glow": "\033[38;5;221m",        # 极光黄
        "icon_glow": "\033[38;5;81m",         # 亮青
        "accent_pulse": "\033[38;5;214m",     # 琥珀
    },

    # ── Catppuccin Mocha 主题（暖暗紫柔和风）────────
    "catppuccin": {
        # ── 基础语义键（Catppuccin Mocha 调色板） ──
        "title": "\033[38;5;183m",         # 紫 #CBA6F7
        "subtitle": "\033[38;5;242m",      # 中灰
        "prompt": "\033[38;5;117m",        # 蓝 #89B4FA
        "user": "\033[38;5;117m",          # 蓝 #89B4FA
        "assistant": "\033[38;5;120m",     # 绿 #A6E3A1
        "thinking": "\033[38;5;242m",      # 中灰
        "tool": "\033[38;5;242m",          # 中灰
        "success": "\033[38;5;120m",       # 绿 #A6E3A1
        "warning": "\033[38;5;222m",       # 黄 #F9E2AF
        "error": "\033[38;5;210m",         # 红 #F38BA8
        "info": "\033[38;5;242m",          # 中灰
        "cost": "\033[38;5;242m",          # 中灰
        "separator": "\033[38;5;237m",     # surface1 #45475A
        "meta": "\033[38;5;242m",          # 中灰
        "accent": "\033[38;5;183m",        # 紫 #CBA6F7
        "border": "\033[38;5;237m",        # surface1 #45475A
        "highlight": "\033[38;5;117m",     # 蓝 #89B4FA
        "muted": "\033[38;5;236m",         # surface0 #313244
        "code": "\033[38;5;242m",          # 中灰
        "divider": "\033[38;5;237m",       # surface1 #45475A
        # ── progress / diff / border / overlay ──
        "progress_filled": "\033[38;5;120m",
        "progress_empty": "\033[38;5;236m",
        "diff_add": "\033[38;5;120m",
        "diff_del": "\033[38;5;210m",
        "diff_ctx": "\033[38;5;242m",
        "border_active": "\033[38;5;117m",
        "border_inactive": "\033[38;5;237m",
        "overlay_bg": "\033[48;5;235m",
        "tag_code": "\033[38;5;222m",
        # ── glow / pulse 发光语义 ──
        "prompt_glow": "\033[38;5;117m",      # 蓝
        "border_glow": "\033[38;5;183m",      # 紫
        "status_pulse": "\033[38;5;222m",     # 黄
        "breathing_base": "\033[38;5;26m",    # 暗蓝
        "pulse_highlight": "\033[38;5;75m",   # 亮蓝
        "separator_glow": "\033[38;5;183m",   # 紫
        "tag_glow": "\033[38;5;117m",         # 蓝
        # ── 动效语义键 ──
        "effect_bounce": "\033[38;5;183m",    # 紫
        "effect_wave": "\033[38;5;140m",      # 中紫
        "effect_sparkle": "\033[38;5;75m",    # 亮蓝
        "effect_glow": "\033[38;5;222m",      # 黄
        "effect_shimmer": "\033[38;5;195m",   # 亮白青
        # ── 新增美化语义键 ──
        "user_glow": "\033[38;5;75m",         # 亮蓝
        "placeholder_glow": "\033[38;5;183m", # 紫
        "border_breath": "\033[38;5;26m",     # 暗蓝
        "model_breath": "\033[38;5;183m",     # 紫
        "tag_breath": "\033[38;5;117m",       # 蓝
        "effect_border": "\033[38;5;140m",    # 中紫
        "deco_glow": "\033[38;5;222m",        # 黄
        "icon_glow": "\033[38;5;75m",         # 亮蓝
        "accent_pulse": "\033[38;5;214m",     # 琥珀
    },

    # ── Solarized Dark 主题（深褐温和对比风）──────────
    "solarized-dark": {
        # ── 基础语义键（Solarized 调色板） ──
        "title": "\033[38;5;33m",          # 蓝 #268BD2
        "subtitle": "\033[38;5;242m",      # 中灰
        "prompt": "\033[38;5;37m",         # 青 #2AA198
        "user": "\033[38;5;37m",           # 青 #2AA198
        "assistant": "\033[38;5;64m",      # 绿 #859900
        "thinking": "\033[38;5;242m",      # 中灰
        "tool": "\033[38;5;242m",          # 中灰
        "success": "\033[38;5;64m",        # 绿 #859900
        "warning": "\033[38;5;136m",       # 黄 #B58900
        "error": "\033[38;5;160m",         # 红 #DC322F
        "info": "\033[38;5;242m",          # 中灰
        "cost": "\033[38;5;242m",          # 中灰
        "separator": "\033[38;5;235m",     # base02 #073642
        "meta": "\033[38;5;242m",          # 中灰
        "accent": "\033[38;5;136m",        # 黄 #B58900
        "border": "\033[38;5;235m",        # base02 #073642
        "highlight": "\033[38;5;37m",      # 青 #2AA198
        "muted": "\033[38;5;234m",         # base03 #002B36
        "code": "\033[38;5;242m",          # 中灰
        "divider": "\033[38;5;235m",       # base02 #073642
        # ── progress / diff / border / overlay ──
        "progress_filled": "\033[38;5;64m",
        "progress_empty": "\033[38;5;236m",
        "diff_add": "\033[38;5;64m",
        "diff_del": "\033[38;5;160m",
        "diff_ctx": "\033[38;5;242m",
        "border_active": "\033[38;5;37m",
        "border_inactive": "\033[38;5;235m",
        "overlay_bg": "\033[48;5;234m",
        "tag_code": "\033[38;5;136m",
        # ── glow / pulse 发光语义 ──
        "prompt_glow": "\033[38;5;37m",       # 青
        "border_glow": "\033[38;5;33m",       # 蓝
        "status_pulse": "\033[38;5;136m",     # 黄
        "breathing_base": "\033[38;5;25m",    # 暗蓝
        "pulse_highlight": "\033[38;5;69m",   # 亮蓝
        "separator_glow": "\033[38;5;33m",    # 蓝
        "tag_glow": "\033[38;5;37m",          # 青
        # ── 动效语义键 ──
        "effect_bounce": "\033[38;5;37m",     # 青
        "effect_wave": "\033[38;5;33m",       # 蓝
        "effect_sparkle": "\033[38;5;69m",    # 亮蓝
        "effect_glow": "\033[38;5;136m",      # 黄
        "effect_shimmer": "\033[38;5;195m",   # 亮白青
        # ── 新增美化语义键 ──
        "user_glow": "\033[38;5;69m",         # 亮蓝
        "placeholder_glow": "\033[38;5;37m",  # 青
        "border_breath": "\033[38;5;23m",     # 暗青
        "model_breath": "\033[38;5;33m",      # 蓝
        "tag_breath": "\033[38;5;37m",        # 青
        "effect_border": "\033[38;5;33m",     # 蓝
        "deco_glow": "\033[38;5;136m",        # 黄
        "icon_glow": "\033[38;5;69m",         # 亮蓝
        "accent_pulse": "\033[38;5;214m",     # 琥珀
    },

    # ── Monokai 主题（高饱和荧光风）──────────────────
    "monokai": {
        # ── 基础语义键（Monokai Pro 调色板） ──
        "title": "\033[38;5;141m",         # 紫 #AE81FF
        "subtitle": "\033[38;5;242m",      # 中灰
        "prompt": "\033[38;5;81m",         # 蓝 #66D9EF
        "user": "\033[38;5;81m",           # 蓝 #66D9EF
        "assistant": "\033[38;5;83m",      # 绿 #A6E22E
        "thinking": "\033[38;5;242m",      # 中灰
        "tool": "\033[38;5;242m",          # 中灰
        "success": "\033[38;5;83m",        # 绿 #A6E22E
        "warning": "\033[38;5;228m",       # 黄 #E6DB74
        "error": "\033[38;5;197m",         # 粉红 #F92672
        "info": "\033[38;5;242m",          # 中灰
        "cost": "\033[38;5;242m",          # 中灰
        "separator": "\033[38;5;237m",     # 背景 #272822
        "meta": "\033[38;5;242m",          # 中灰
        "accent": "\033[38;5;228m",        # 黄 #E6DB74
        "border": "\033[38;5;237m",        # 背景 #272822
        "highlight": "\033[38;5;81m",      # 蓝 #66D9EF
        "muted": "\033[38;5;236m",         # 选择 #49483E
        "code": "\033[38;5;242m",          # 中灰
        "divider": "\033[38;5;237m",       # 背景 #272822
        # ── progress / diff / border / overlay ──
        "progress_filled": "\033[38;5;83m",
        "progress_empty": "\033[38;5;236m",
        "diff_add": "\033[38;5;83m",
        "diff_del": "\033[38;5;197m",
        "diff_ctx": "\033[38;5;242m",
        "border_active": "\033[38;5;81m",
        "border_inactive": "\033[38;5;237m",
        "overlay_bg": "\033[48;5;235m",
        "tag_code": "\033[38;5;228m",
        # ── glow / pulse 发光语义 ──
        "prompt_glow": "\033[38;5;81m",       # 蓝
        "border_glow": "\033[38;5;141m",      # 紫
        "status_pulse": "\033[38;5;228m",     # 黄
        "breathing_base": "\033[38;5;32m",    # 暗青
        "pulse_highlight": "\033[38;5;81m",   # 亮青
        "separator_glow": "\033[38;5;141m",   # 紫
        "tag_glow": "\033[38;5;81m",          # 蓝
        # ── 动效语义键 ──
        "effect_bounce": "\033[38;5;141m",    # 紫
        "effect_wave": "\033[38;5;81m",       # 蓝
        "effect_sparkle": "\033[38;5;51m",    # 最亮青
        "effect_glow": "\033[38;5;228m",      # 黄
        "effect_shimmer": "\033[38;5;195m",   # 亮白青
        # ── 新增美化语义键 ──
        "user_glow": "\033[38;5;81m",         # 蓝
        "placeholder_glow": "\033[38;5;141m", # 紫
        "border_breath": "\033[38;5;23m",     # 暗青
        "model_breath": "\033[38;5;141m",     # 紫
        "tag_breath": "\033[38;5;81m",        # 蓝
        "effect_border": "\033[38;5;81m",     # 蓝
        "deco_glow": "\033[38;5;228m",        # 黄
        "icon_glow": "\033[38;5;51m",         # 最亮青
        "accent_pulse": "\033[38;5;214m",     # 琥珀
    },
}

# ══════════════════════════════════════════════════════════
# 当前活动主题
# ══════════════════════════════════════════════════════════

_ACTIVE_NAME: str = "dark"
THEME: Dict[str, str] = dict(THEMES["dark"])  # 可变的当前主题副本


def set_theme(name: str) -> None:
    """切换到指定主题。

    Args:
        name: 主题名称（"dark" / "light" / "high-contrast" / "nord" / "catppuccin" / "solarized-dark" / "monokai"）

    Raises:
        ValueError: 主题名称不存在（可用: dark, light, high-contrast, nord, catppuccin, solarized-dark, monokai）
    """
    if name not in THEMES:
        raise ValueError(f"未知主题: {name}，可用主题: {', '.join(THEMES.keys())}")
    global _ACTIVE_NAME, THEME
    _ACTIVE_NAME = name
    THEME.clear()
    THEME.update(THEMES[name])


def get_active_theme() -> str:
    """返回当前主题名称。"""
    return _ACTIVE_NAME


def list_themes() -> List[str]:
    """返回所有可用主题名称列表。"""
    return list(THEMES.keys())


def get_theme_names_with_desc() -> List[tuple[str, str]]:
    """返回 (主题名, 简短描述) 列表。"""
    return [
        ("dark", "深色主题（默认）"),
        ("light", "亮色主题（浅色背景用）"),
        ("high-contrast", "高对比主题（高可读性）"),
        ("nord", "Nord 极光主题（蓝灰冰霜风）"),
        ("catppuccin", "Catppuccin Mocha 主题（暖暗紫柔和风）"),
        ("solarized-dark", "Solarized Dark 主题（深褐温和对比风）"),
        ("monokai", "Monokai 主题（高饱和荧光风）"),
    ]


__all__ = [
    "THEME", "THEMES",
    "set_theme", "get_active_theme", "list_themes",
    "get_theme_names_with_desc",
]
