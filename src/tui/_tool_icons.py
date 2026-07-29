"""工具图标 & Agent 类型标签 — 保持与旧版本一致的显示效果。

2026-07-29 TUI 重构：原 core/tool_icons.py 已删除，此处提供完整映射。
"""

from __future__ import annotations

# ── Agent 类型缩写（与旧版本一致：小写 2 字符） ──────
AGENT_TYPE_ABBREV: dict[str, str] = {
    "map":     "mp",
    "review":  "rv",
    "plan":    "pl",
    "execute": "ex",
}

# ── Agent 类型 → 256 色 ANSI ──────────────────────────
AGENT_TYPE_COLORS: dict[str, str] = {
    "map":     "\033[38;5;33m",    # 深蓝
    "review":  "\033[38;5;129m",   # 紫
    "plan":    "\033[38;5;214m",   # 琥珀
    "execute": "\033[38;5;208m",   # 橙色
}

# ── 工具图标（与旧版本一致的 Unicode 图标） ─────────
TOOL_ICONS: dict[str, str] = {
    "bash":              "\u26a1",   # 闪电
    "execute_command":   "\u26a1",   # 闪电
    "read_file":         "\U0001f4d6",  # 书
    "write_file":        "\u270e",   # 笔
    "update_file":       "\u270e",   # 笔
    "str_replace_editor": "\u270e",  # 笔
    "file_editor":       "\u270e",   # 笔
    "dispatch_agent":    "\u2699",   # 齿轮
    "user_select":       "\u2753",   # 问号
    "web_search":        "\U0001f310",  # 地球
    "web_fetch":         "\U0001f310",  # 地球
    "rm":                "\u2715",   # 叉号
    "grep":              "\u2315",   # 搜索镜
    "find":              "\u2315",   # 搜索镜
    "glob":              "\u2315",   # 搜索镜
}

__all__ = ["AGENT_TYPE_ABBREV", "AGENT_TYPE_COLORS", "TOOL_ICONS"]
