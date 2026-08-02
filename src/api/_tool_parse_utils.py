"""工具调用格式转换工具函数 — 从 stream_parse.py 提取而来

纯函数模块（无副作用、无可变全局状态），包含：
- convert_tool_calls_map / convert_tool_calls_map_with_status（流式路径）
- parse_raw_tool_calls / parse_raw_tool_calls_with_status（非流式路径）

_with_status 变体在原返回值基础上额外返回解析失败的 tool_call_id 列表，
供上层 _retry_on_parse_failure_async 检测并触发解析重试。
旧函数（无 _with_status 后缀）通过内部委托 _with_status 变体并丢弃失败 ID，
保持接口向后兼容。
"""
from __future__ import annotations

import json
import logging

from .json_repair import json_loads_safe

_logger = logging.getLogger(__name__)


def full_args_str(tc: dict) -> str:
    """工具调用条目完整参数串（流式 `_args_parts` list 或 legacy `arguments`）。

    tool_calls 流式累积改 list（避免超长参数 O(n²) 拼接）——完整串在消费方
    需要时 join，此处统一读取入口。
    """
    parts = tc.get("_args_parts")
    if parts:
        return "".join(parts)
    return tc.get("arguments", "")


def convert_tool_calls_map_with_status(tool_calls_map):
    """将流式累积的工具调用映射 {index: {...}} 转换为列表格式，同时返回解析失败 ID。

    Returns:
        (tool_calls, failed_ids)
        - tool_calls: [{"id": str, "name": str, "arguments": dict}]
        - failed_ids: [tool_call_id, ...] 解析失败的 tool_call_id 列表
    """
    tool_calls = []
    failed_ids: list[str] = []
    for idx in sorted(tool_calls_map.keys()):
        tc = tool_calls_map[idx]
        try:
            args_str = full_args_str(tc)
            args = json_loads_safe(args_str)[0] if args_str else {}
        except json.JSONDecodeError as e:
            tool_id = tc.get("_stream_label") or tc.get("id") or f"auto_{idx}"
            failed_ids.append(tool_id)
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
    return tool_calls, failed_ids


def convert_tool_calls_map(tool_calls_map):
    """将流式累积的工具调用映射 {index: {...}} 转换为列表格式。

    Returns:
        [{"id": str, "name": str, "arguments": dict}]
    """
    tool_calls, _failed_ids = convert_tool_calls_map_with_status(tool_calls_map)
    return tool_calls


def parse_raw_tool_calls_with_status(raw_tool_calls):
    """解析原始 JSON dict 格式的工具调用列表，同时返回解析失败 ID。

    Args:
        raw_tool_calls: [{"id": str, "function": {"name": str, "arguments": str}}]

    Returns:
        (tool_calls, total_args_str, names, failed_ids)
        - tool_calls: [{"id": str, "name": str, "arguments": dict}]
        - total_args_str: str
        - names: [str, ...]
        - failed_ids: [tool_call_id, ...] 解析失败的 tool_call_id 列表
    """
    tool_calls = []
    total_args = ""
    names = []
    failed_ids: list[str] = []
    for tc in raw_tool_calls:
        func = tc.get("function") or {}
        args_str = func.get("arguments", "")
        args = {}
        if args_str:
            try:
                args, _ = json_loads_safe(args_str)
                total_args += args_str
            except json.JSONDecodeError as e:
                tool_id = tc.get("id") or f"auto_{len(failed_ids) + len(tool_calls)}"
                failed_ids.append(tool_id)
                _logger.warning("JSON解析失败，已跳过 tool_call(id=%s): %s, 参数: %s",
                                tool_id, e, args_str[:100] if args_str else "")
                continue
        tool_calls.append({
            "id": tc.get("id", ""),
            "name": func.get("name", ""),
            "arguments": args if isinstance(args, dict) else {},
        })
        name = func.get("name", "")
        if name:
            names.append(name)
    return tool_calls, total_args, names, failed_ids


def parse_raw_tool_calls(raw_tool_calls):
    """解析原始 JSON dict 格式的工具调用列表。

    Args:
        raw_tool_calls: [{"id": str, "function": {"name": str, "arguments": str}}]

    Returns:
        ([{"id": str, "name": str, "arguments": dict}], total_args_str, names)
    """
    tool_calls, total_args, names, _failed_ids = parse_raw_tool_calls_with_status(raw_tool_calls)
    return tool_calls, total_args, names
