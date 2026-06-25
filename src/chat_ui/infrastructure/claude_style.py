"""Claude Code CLI 风格样式常量与门控模块。

提供 Claude Code 风格的图标、颜色、Spinner 帧集合和工具调用卡片样式。
所有 Claude Code 风格特性通过 CHAT_UI_CLAUDE_STYLE 环境变量门控，
默认关闭，用户按需启用。

门控层级：
  CHAT_UI_CLAUDE_STYLE           — 全局主开关
  CHAT_UI_CLAUDE_STYLE_<FEATURE> — 子功能细粒度开关（如 THINKING / TOOLS）

使用示例:
    from ..infrastructure.claude_style import (
        _is_claude_style_enabled,
        CLAUDE_THINKING_ICON,
        CLAUDE_COLORS,
    )
"""

from __future__ import annotations

import os
from typing import Any

from .ansi import ANSI_DIM, ANSI_CYAN, ANSI_RESET, style

# ── 门控函数 ─────────────────────────────────────────────


def _is_claude_style_enabled() -> bool:
    """检查 Claude Code 风格全局开关。

    优先通过 FeatureFlags 统一注册表读取，失败时回退到
    直接读取 CHAT_UI_CLAUDE_STYLE 环境变量。

    Returns:
        True 当 CHAT_UI_CLAUDE_STYLE 为启用的真值。
    """
    try:
        from src.shared_events.feature_flags import get_feature_flags
        return get_feature_flags().chat_ui_claude_style
    except Exception:
        return os.environ.get("CHAT_UI_CLAUDE_STYLE", "").strip().lower() in (
            "1", "true", "yes", "on"
        )


def _is_feature_enabled(feature_name: str) -> bool:
    """检查 Claude Code 风格子功能开关。

    通过 CHAT_UI_CLAUDE_STYLE_<FEATURE> 环境变量控制。
    若子功能未显式设置，回退到全局主开关。

    Args:
        feature_name: 子功能名称（如 "THINKING"、"TOOLS"），
                      对应环境变量 CHAT_UI_CLAUDE_STYLE_<feature_name>。

    Returns:
        True 当子功能或全局开关启用。
    """
    env_key = f"CHAT_UI_CLAUDE_STYLE_{feature_name.upper()}"
    env_val = os.environ.get(env_key, "").strip().lower()
    if env_val in ("1", "true", "yes", "on"):
        return True
    if env_val in ("0", "false", "no", "off"):
        return False
    # 子功能未显式设置时，回退到全局主开关
    return _is_claude_style_enabled()


# ── Claude Code 主题图标 ─────────────────────────────────


CLAUDE_THINKING_ICON: str = "\u23fa"    # ⏺ — 思考图标
CLAUDE_PROMPT_ICON: str = "\u276f"      # ❯ — 提示符图标
CLAUDE_SUCCESS_ICON: str = "\u2713"     # ✓ — 成功图标
CLAUDE_FAIL_ICON: str = "\u2717"        # ✗ — 失败图标


# ── 工具图标映射 ─────────────────────────────────────────


CLAUDE_TOOL_ICONS: dict[str, str] = {
    "read_file":   "\U0001f4d6",  # 📖
    "search":      "\U0001f50d",  # 🔍
    "bash":        "\U0001f4bb",  # 💻
    "write_file":  "\U0001f4dd",  # 📝
    "edit":        "\u270f\ufe0f",  # ✏️
    "find":        "\U0001f50e",  # 🔎
    "ls":          "\U0001f4c2",  # 📂
    "mk":          "\U0001f4c1",  # 📁
    "rm":          "\U0001f5d1",  # 🗑
    "mv":          "\U0001f4e6",  # 📦
    "cp":          "\U0001f4cb",  # 📋
    "user_select": "\U0001f464",  # 👤
    "dispatch_agent": "\U0001f916",  # 🤖
    "web_search":  "\U0001f310",  # 🌐
}


# ── Claude Code 颜色风格 ─────────────────────────────────


CLAUDE_COLORS: dict[str, str | Any] = {
    "thinking": style("", dim=True, italic=True).rstrip(ANSI_RESET),
    "tool_call": ANSI_CYAN,
    "success":   style("", fg="green").rstrip(ANSI_RESET),
    "error":     style("", fg="red").rstrip(ANSI_RESET),
    "muted":     ANSI_DIM,
    "highlight": style("", bold=True).rstrip(ANSI_RESET),
}


# ── Claude Code Spinner 预设 ─────────────────────────────


CLAUDE_SPINNER_FRAMES: list[str] = [
    "\u280b",  # ⠋
    "\u2819",  # ⠙
    "\u2839",  # ⠹
    "\u2838",  # ⠸
    "\u283c",  # ⠼
    "\u2834",  # ⠴
    "\u2826",  # ⠦
    "\u2827",  # ⠧
    "\u2807",  # ⠇
    "\u280f",  # ⠏
]


# ── 工具调用卡片样式 ─────────────────────────────────────


CLAUDE_TOOL_CARD_STYLE: dict[str, Any] = {
    "border_style": "dim",
    "border_color": "cyan",
    "padding_left": 2,
    "padding_right": 2,
    "indent": 2,
    "expand_symbol": "\u25b6",   # ▶
    "collapse_symbol": "\u25bc",  # ▼
    "max_params_display": 80,
}
