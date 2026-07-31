"""工具图标 & Agent 类型标签 — 保持与旧版本一致的显示效果。

2026-07-29 TUI 重构：原 core/tool_icons.py 已删除，此处提供完整映射。

工具名→展示映射唯一真源（2026-07-31 方向F 收敛）：
  - TOOL_ICONS（工具名→图标）
  - TOOL_CATEGORY_MAP / TOOL_CATEGORY_COLORS（工具名→类别→类别配色，收敛自
    _subagent_panel.py 的 _TOOL_CATEGORY_MAP/_TOOL_CATEGORY_COLORS）
  - AGENT_TYPE_ABBREV / AGENT_TYPE_COLORS（Agent 类型→缩写/颜色）
映射 import 后只读，Python 字典读操作在 GIL 下线程安全；
原 _subagent_panel 线程隔离本地副本已收敛（无共享写风险）。
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

# ── 工具名 → 类别（唯一真源，方向F 步骤12 收敛） ──────────
TOOL_CATEGORY_MAP: dict[str, str] = {
    "bash": "shell", "execute_command": "shell",
    "read_file": "file_read",
    "write_file": "file_write", "update_file": "file_write",
    "str_replace_editor": "file_write", "file_editor": "file_write",
    "grep": "search", "find": "search", "glob": "search",
    "web_search": "search", "web_fetch": "search",
    "dispatch_agent": "agent",
    "user_select": "interact",
    "rm": "delete",
}

# ── 工具类别 → 256 色 ANSI（唯一真源，方向F 步骤12 收敛） ──
TOOL_CATEGORY_COLORS: dict[str, str] = {
    "shell":      "\033[38;5;41m",
    "file_read":  "\033[38;5;81m",
    "file_write": "\033[38;5;213m",
    "search":     "\033[38;5;221m",
    "agent":      "\033[38;5;75m",
    "interact":   "\033[38;5;51m",
    "delete":     "\033[38;5;203m",
}

__all__ = [
    "AGENT_TYPE_ABBREV",
    "AGENT_TYPE_COLORS",
    "TOOL_ICONS",
    "TOOL_CATEGORY_MAP",
    "TOOL_CATEGORY_COLORS",
]
