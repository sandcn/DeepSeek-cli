"""
工具颜色和图标主题定义
"""

# ── 工具 → 颜色名（兼容旧接口） ──────────────────────────
TOOL_COLORS = {
    "bash": "green",
    "read_file": "cyan",
    "write_file": "magenta",
    "update_file": "magenta",
    "str_replace_editor": "magenta",
    "dispatch_agent": "blue",
    "user_select": "cyan",
    "web_search": "blue",
    "rm": "red",
    "execute_command": "green",
    "file_editor": "magenta",
    "grep": "yellow",
    "find": "yellow",
    "glob": "yellow",
}

# ── SubAgent 类型 → 两字符缩写 ──────────────────────────
AGENT_TYPE_ABBREV = {
    "map": "mp",
    "review": "rv",
    "think": "th",
    "plan": "pl",
    "read_memory": "rm",
    "write_memory": "wm",
    "execute": "ex",
}

# ── SubAgent 类型 → 256色 ANSI 前景色 ──────────────────
# 使用 256 色调色板：mp=蓝色(33) rv=紫色(129) pl=琥珀色(214) rm=青色(45) wm=黄色(226)
AGENT_TYPE_COLORS = {
    "map": "\033[38;5;33m",        # 深蓝
    "review": "\033[38;5;129m",    # 紫
    "think": "\033[38;5;49m",      # 薄荷绿/亮青
    "plan": "\033[38;5;214m",      # 琥珀
    "read_memory": "\033[38;5;45m",   # 青色
    "write_memory": "\033[38;5;226m", # 黄色
    "execute": "\033[38;5;208m",   # 橙色
}

# ── 工具类别 → 256色 ANSI 前景色 ──────────────────────
TOOL_CATEGORY_COLORS = {
    "shell": "\033[38;5;41m",      # 终端命令 — 翠绿
    "file_read": "\033[38;5;81m",  # 文件读取 — 天蓝
    "file_write": "\033[38;5;213m", # 文件写入 — 粉红
    "search": "\033[38;5;221m",    # 搜索 — 金色
    "agent": "\033[38;5;75m",      # 子Agent派发 — 浅蓝
    "interact": "\033[38;5;51m",   # 用户交互 — 青色
    "delete": "\033[38;5;203m",    # 删除 — 橙红
}

# ── 工具 → 类别映射 ─────────────────────────────────────
_TOOL_CATEGORY = {
    "bash": "shell",
    "execute_command": "shell",
    "read_file": "file_read",
    "write_file": "file_write",
    "update_file": "file_write",
    "str_replace_editor": "file_write",
    "file_editor": "file_write",
    "grep": "search",
    "find": "search",
    "glob": "search",
    "web_search": "search",
    "dispatch_agent": "agent",
    "user_select": "interact",
    "rm": "delete",
}


def get_tool_color(tool_name: str) -> str:
    """返回工具的 256色 ANSI 前景色，未知工具返回默认暗色。"""
    cat = _TOOL_CATEGORY.get(tool_name)
    if cat:
        return TOOL_CATEGORY_COLORS[cat]
    return "\033[38;5;245m"  # 灰色兜底


# ── 工具图标 ────────────────────────────────────────────
TOOL_ICONS = {
    "bash": "⚡",         # 闪电 - 命令执行
    "execute_command": "⚡",  # 闪电 - 命令执行
    "read_file": "📖",    # 书 - 读取文件
    "write_file": "✎",    # 笔 - 写入文件
    "update_file": "✎",   # 笔 - 修改文件
    "str_replace_editor": "✎",  # 笔 - 编辑
    "dispatch_agent": "⚙",  # 齿轮 - 派发子代理
    "user_select": "❓",   # 问号 - 用户选择
    "web_search": "🌐",   # 地球 - 搜索
    "rm": "✕",            # 叉号 - 删除
    "grep": "⌕",          # 搜索镜 - 搜索
    "find": "⌕",          # 搜索镜 - 搜索
    "glob": "⌕",          # 搜索镜 - 搜索
    "unknown": "·",
}
