"""工具参数格式化 — 提取工具参数用于显示（core 层纯工具函数）。

2026-07-31 TUI 架构改进步骤 6：从 src/tui/_param_formatter.py 迁回 core 层。
原文件为 TUI 重构期临时存根，现归位为 core 层纯函数模块（无 UI 副作用），
由 src/core/tool_executor_async.py 消费。

提供 extract_key_params 函数（对齐 Claude Code 工具卡参数显示，非 JSON）：
- 已知工具名：关键参数**值**（纯值空格连接，如 read_file → `pyproject.toml`）；
- 其他工具/show_all：紧凑 ``k=v`` 空格连接（非 JSON 大括号，截断至 80 字符）。
"""

from __future__ import annotations

import json
from typing import Any


def extract_key_params(
    tool_name: str,
    arguments: dict[str, Any] | str,
    show_all: bool = False,
) -> str:
    """从工具参数中提取关键参数用于显示（纯参数值，非 JSON）。

    - 已知工具名：关键参数值（纯值空格连接，对齐 Claude Code `Read pyproject.toml`）；
    - 未知工具/show_all：紧凑 ``k=v`` 空格连接（不输出 JSON 大括号，防参数字符串
      膨胀破坏工具卡顶边框宽度约束），截断至 80 字符。
    """
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except (json.JSONDecodeError, TypeError):
            return str(arguments)[:80]

    if not isinstance(arguments, dict) or not arguments:
        return ""

    # 已知工具的参数裁剪映射（对齐 Claude Code：显示关键参数值）
    KEY_PARAMS: dict[str, list[str]] = {
        "read_file": ["path"],
        "write_file": ["path"],
        "update_file": ["path"],
        "str_replace_editor": ["path"],
        "file_editor": ["path"],
        "search": ["query", "path"],
        "find": ["pattern", "path"],
        "grep": ["pattern", "path"],
        "glob": ["pattern"],
        "bash": ["command"],
        "execute_command": ["command"],
        "ls": ["path"],
        "mkdir": ["path"],
        "rm": ["path"],
        "mv": ["source", "destination"],
        "cp": ["source", "destination"],
        "web_search": ["query"],
        "web_fetch": ["url"],
        "dispatch_agent": ["description", "type"],
        "user_select": ["title"],
    }

    keys = KEY_PARAMS.get(tool_name)
    if keys and not show_all:
        # 已知工具：关键参数**值**（纯值，空格连接）
        parts = []
        for k in keys:
            v = arguments.get(k)
            if v is not None:
                s = str(v)
                if len(s) > 60:
                    s = s[:57] + "..."
                parts.append(s)
        return " ".join(parts)

    # 未知工具 / show_all：紧凑 `k=v` 空格连接（非 JSON 大括号）
    parts = []
    for k, v in arguments.items():
        s = str(v)
        if len(s) > 40:
            s = s[:37] + "..."
        parts.append(f"{k}={s}")
    result = " ".join(parts)
    if len(result) > 80:
        result = result[:77] + "..."
    return result


__all__ = ["extract_key_params"]
