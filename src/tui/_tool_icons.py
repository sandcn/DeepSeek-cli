"""工具图标 & Agent 类型标签 — 兼容保留（Claude Code 极简样式后仅兼容引用）。

2026-08-06 用户需求（所有 tool card 对齐 Claude Code）：工具卡/子代理面板
**不再显示 emoji 工具图标**（标题行/工具记录行去掉 ⚡/📖/✏️ 等）——
TOOL_ICONS 保留为兼容 re-export（外部测试/调用面），生产渲染不再消费；
TOOL_CATEGORY_MAP / TOOL_CATEGORY_STYLES（类别配色）继续为唯一真源
（工具名按类别着色）。

工具名→展示映射（2026-07-31 方向F 收敛）：
  - TOOL_ICONS（工具名→图标；极简样式后仅兼容保留，渲染不再使用）
  - TOOL_CATEGORY_MAP / TOOL_CATEGORY_STYLES（工具名→类别→类别配色）
  - AGENT_TYPE_ABBREV / AGENT_TYPE_STYLES（Agent 类型→缩写/颜色）
映射 import 后只读，Python 字典读操作在 GIL 下线程安全；
原 _subagent_panel 线程隔离本地副本已收敛（无共享写风险）。

★ 标准 React Ink 组件化（2026-08-05）：配色映射从「ANSI 色串」迁移为
「Style 对象」（``Style(fg=色号)``）——消除渲染侧的 ANSI 中间层解析
（``_subagent_render`` 不再 ``_ansi_color_code`` 从字符串提取色号）。
旧 ANSI 色串常量（``AGENT_TYPE_COLORS`` / ``TOOL_CATEGORY_COLORS``）
保留为兼容 re-export（既有测试/外部调用面），值为同一色号的 ANSI 序列。
"""

from __future__ import annotations

from src.tui.core.style import Style

# ── Agent 类型缩写（与旧版本一致：小写 2 字符） ──────
AGENT_TYPE_ABBREV: dict[str, str] = {
    "map":     "mp",
    "review":  "rv",
    "plan":    "pl",
    "execute": "ex",
}

# ── Agent 类型 → Style（唯一真源，标准 React Ink） ─────
AGENT_TYPE_STYLES: dict[str, Style] = {
    "map":     Style(fg=33),    # 深蓝
    "review":  Style(fg=129),   # 紫
    "plan":    Style(fg=214),   # 琥珀
    "execute": Style(fg=208),   # 橙色
}

# ── Agent 类型 → 256 色 ANSI（兼容 re-export，同一色号） ──
AGENT_TYPE_COLORS: dict[str, str] = {
    k: f"\033[38;5;{s.fg}m" for k, s in AGENT_TYPE_STYLES.items()
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

# ── 工具类别 → Style（唯一真源，标准 React Ink） ──
TOOL_CATEGORY_STYLES: dict[str, Style] = {
    "shell":      Style(fg=41),
    "file_read":  Style(fg=81),
    "file_write": Style(fg=213),
    "search":     Style(fg=221),
    "agent":      Style(fg=75),
    "interact":   Style(fg=51),
    "delete":     Style(fg=203),
}

# ── 工具类别 → 256 色 ANSI（兼容 re-export，同一色号） ──
TOOL_CATEGORY_COLORS: dict[str, str] = {
    k: f"\033[38;5;{s.fg}m" for k, s in TOOL_CATEGORY_STYLES.items()
}

__all__ = [
    "AGENT_TYPE_ABBREV",
    "AGENT_TYPE_STYLES",
    "AGENT_TYPE_COLORS",
    "TOOL_ICONS",
    "TOOL_CATEGORY_MAP",
    "TOOL_CATEGORY_STYLES",
    "TOOL_CATEGORY_COLORS",
]
