"""工具参数格式化存根 — 替换已删除的 core/param_formatter.py。

2026-07-29 TUI 重构：原文件已随 core/ 目录清理被删除，
此处提供最小化 extract_key_params 函数。
"""

from __future__ import annotations

import json
from typing import Any


def extract_key_params(
    tool_name: str,
    arguments: dict[str, Any] | str,
    show_all: bool = False,
) -> str:
    """从工具参数中提取关键参数用于显示。

    原 param_formatter.py 的核心函数，重构后精简为最小实现：
    - 对已知工具名做参数裁剪
    - 其他工具展示全部参数（截断至 80 字符）
    """
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except (json.JSONDecodeError, TypeError):
            return str(arguments)[:80]

    if not isinstance(arguments, dict) or not arguments:
        return ""

    # 已知工具的参数裁剪映射
    KEY_PARAMS: dict[str, list[str]] = {
        "read_file": ["path"],
        "write_file": ["path"],
        "update_file": ["path"],
        "search": ["query", "path"],
        "find": ["pattern", "path"],
        "bash": ["command"],
        "ls": ["path"],
        "mkdir": ["path"],
        "rm": ["path"],
        "mv": ["source", "destination"],
        "cp": ["source", "destination"],
        "web_search": ["query"],
        "dispatch_agent": ["description", "type"],
        "user_select": ["title"],
    }

    keys = KEY_PARAMS.get(tool_name)
    if keys and not show_all:
        parts = []
        for k in keys:
            v = arguments.get(k)
            if v is not None:
                s = str(v)
                if len(s) > 60:
                    s = s[:57] + "..."
                parts.append(f"{k}={s}")
        return ", ".join(parts)

    # 展示全部参数
    s = json.dumps(arguments, ensure_ascii=False)
    if len(s) > 80:
        s = s[:77] + "..."
    return s


__all__ = ["extract_key_params"]
