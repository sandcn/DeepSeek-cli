"""工具调用格式转换工具函数 — 从 stream_parse.py 提取而来

包含 convert_tool_calls_map 和 parse_raw_tool_calls 两个纯函数。
"""
from __future__ import annotations

import json
import logging

from .json_repair import json_loads_safe

_logger = logging.getLogger(__name__)


def convert_tool_calls_map(tool_calls_map):
    """将流式累积的工具调用映射 {index: {...}} 转换为列表格式。

    Returns:
        [{"id": str, "name": str, "arguments": dict}]
    """
    tool_calls = []
    for idx in sorted(tool_calls_map.keys()):
        tc = tool_calls_map[idx]
        try:
            args_str = tc.get("arguments", "")
            args = json_loads_safe(args_str)[0] if args_str else {}
        except json.JSONDecodeError as e:
            _logger.warning("JSON解析失败，已跳过 tool_call(id=%s): %s, 参数: %s",
                            tc.get("id", "?"), e,
                            args_str[:100] if args_str else "")
            continue
        tool_id = tc.get("_stream_label") or tc.get("id") or f"auto_{idx}"
        tool_calls.append({
            "id": tool_id,
            "name": tc.get("name", ""),
            "arguments": args if isinstance(args, dict) else {}
        })
    return tool_calls


def parse_raw_tool_calls(raw_tool_calls):
    """解析原始 JSON dict 格式的工具调用列表。

    Args:
        raw_tool_calls: [{"id": str, "function": {"name": str, "arguments": str}}]

    Returns:
        ([{"id": str, "name": str, "arguments": dict}], total_args_str, names)
    """
    tool_calls = []
    total_args = ""
    names = []
    for tc in raw_tool_calls:
        func = tc.get("function") or {}
        args_str = func.get("arguments", "")
        args = {}
        if args_str:
            try:
                args, _ = json_loads_safe(args_str)
                total_args += args_str
            except json.JSONDecodeError as e:
                _logger.warning("JSON解析失败，已跳过: %s, 参数: %s",
                                e, args_str[:100] if args_str else "")
                continue
        tool_calls.append({
            "id": tc.get("id", ""),
            "name": func.get("name", ""),
            "arguments": args if isinstance(args, dict) else {},
        })
        name = func.get("name", "")
        if name:
            names.append(name)
    return tool_calls, total_args, names
