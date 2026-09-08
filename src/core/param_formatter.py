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

# 已知工具的参数裁剪映射（对齐 Claude Code：显示关键参数值）。
# ★ 2026-08-22（review P2）：提升为模块级常量——原为函数体内局部 dict，
#   每次调用 extract_key_params 都重建整个字面量（trace 每帧
#   _records_from_messages 经 _tool_detail 高频调用），上移后可复用。
#   read_image 与 read_file 同款：显示纯 path 值（修复前走未知工具 k=v → `path=…`）。
_KEY_PARAMS: dict[str, list[str]] = {
    "read_file": ["path"],
    "read_image": ["path"],
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
    "subagent": ["description", "type"],
    "user_select": ["title"],
}


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
        raw = arguments
        try:
            arguments = json.loads(arguments)
        except (json.JSONDecodeError, TypeError):
            return str(raw)[:80]
        # ★ 2026-08-22（review P3-1）：合法 JSON 但顶层非 dict（如 "5"/"[1,2]"/
        #   "null"/"\"str\""）时，json.loads 成功但 arguments 变为非 dict——
        #   原实现静默返回 ""（丢参数值）；与 JSONDecodeError 分支（返回原串）
        #   语义不一致。统一回退原始串。
        if not isinstance(arguments, dict):
            return str(raw)[:80]

    if not arguments:
        return ""

    keys = _KEY_PARAMS.get(tool_name)
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


def _complete_partial_json(s: str):
    """截断流式 JSON 宽容补全解析（提取已到达部分的键值）。

    状态机扫描字符串/转义/括号栈，补全未闭合的引号与括号后 json.loads；
    解析失败或顶层非 dict 返回 None。
    """
    if not s:
        return None
    stack: list[str] = []
    in_string = False
    escape = False
    for ch in s:
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append(ch)
        elif ch == "}":
            if not stack or stack[-1] != "{":
                return None
            stack.pop()
        elif ch == "]":
            if not stack or stack[-1] != "[":
                return None
            stack.pop()
    text = s
    if escape:
        text = text[:-1]
    if in_string:
        text += '"'
    for ch in reversed(stack):
        text += "}" if ch == "{" else "]"
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def extract_key_params_stream(
    tool_name: str,
    arguments: dict[str, Any] | str,
    show_all: bool = False,
) -> str:
    """流式工具参数预览 → 关键参数值摘要（宽容截断 JSON）。

    与 ``extract_key_params`` 同一显示格式（关键参数值，非 JSON），供
    流式解析阶段（ToolParsingEvent 参数逐段到达）实时显示：
    - 完整 JSON / 截断 JSON：宽容补全未闭合引号与括号后提取关键参数值；
    - 非 JSON 文本：回退 ``extract_key_params`` 原串截断路径。
    """
    if isinstance(arguments, dict):
        return extract_key_params(tool_name, arguments, show_all=show_all)
    s = str(arguments or "").strip()
    if not s:
        return ""
    obj = None
    try:
        obj = json.loads(s)
    except (json.JSONDecodeError, TypeError, ValueError):
        obj = _complete_partial_json(s)
    if isinstance(obj, dict):
        return extract_key_params(tool_name, obj, show_all=show_all)
    return extract_key_params(tool_name, s, show_all=show_all)


__all__ = ["extract_key_params", "extract_key_params_stream"]
