"""MCP 工具 → 内置 Func 子类适配。

每个 MCP 工具在注册表中表现为一个动态创建的 ``Func`` 子类：

- 工具名 = ``mcp__<server>__<tool>``（清洗非法字符、限长 64，冲突时追加哈希）
- schema 由 MCP ``inputSchema`` 归一化得到（补齐 ``type: object`` / ``properties``）
- ``execute()`` 委托 ``McpManager.default().call_tool()`` 执行
- 图片结果按当前模型多模态能力转成 ``result_blocks``（image_url data URI）
"""

from __future__ import annotations

import abc
import hashlib
import json
import logging
import re
from typing import Any, Optional

from ..tools.base import Func, ToolMetadata
from .client import McpToolDef

_logger = logging.getLogger(__name__)

#: MCP 工具名前缀（与内置工具区分，便于权限策略与 UI 识别）
MCP_TOOL_PREFIX = "mcp__"
_SERVER_TOOL_SEP = "__"
_MAX_TOOL_NAME = 64
_HASH_LEN = 8

_NAME_SAFE_RE = re.compile(r"[^0-9a-zA-Z_-]")


def sanitize_identifier(value: Any) -> str:
    """清洗标识符：非法字符替换为下划线。"""
    return _NAME_SAFE_RE.sub("_", str(value or ""))


def mcp_tool_name(server_name: str, tool_name: str, disambiguate: bool = False) -> str:
    """生成模型的工具名（合法、唯一、限长）。

    Args:
        server_name: MCP 服务器名。
        tool_name: 服务端工具名。
        disambiguate: 强制追加内容哈希后缀——不同标识经字符清洗后可能碰撞
            （如 ``my server!`` 与 ``my_server_``），碰撞时由调用方置 True
            重新生成，保证注册表不静默覆盖。
    """
    server_part = sanitize_identifier(server_name) or "server"
    tool_part = sanitize_identifier(tool_name) or "tool"
    full = f"{MCP_TOOL_PREFIX}{server_part}{_SERVER_TOOL_SEP}{tool_part}"
    if len(full) <= _MAX_TOOL_NAME and not disambiguate:
        return full
    digest = hashlib.sha1(full.encode("utf-8")).hexdigest()[:_HASH_LEN]
    fixed = len(MCP_TOOL_PREFIX) + len(_SERVER_TOOL_SEP) + 1 + _HASH_LEN
    budget = _MAX_TOOL_NAME - fixed
    server_budget = max(1, min(len(server_part), budget // 2))
    tool_budget = max(1, budget - server_budget)
    return (
        f"{MCP_TOOL_PREFIX}{server_part[:server_budget]}"
        f"{_SERVER_TOOL_SEP}{tool_part[:tool_budget]}_{digest}"
    )


def is_mcp_tool(name: Any) -> bool:
    """工具名是否来自 MCP。"""
    return isinstance(name, str) and name.startswith(MCP_TOOL_PREFIX)


def normalize_input_schema(schema: Any) -> dict:
    """归一化 MCP inputSchema 为 OpenAI function parameters 结构。"""
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    normalized = dict(schema)
    if not isinstance(normalized.get("properties"), dict):
        normalized["properties"] = {}
    normalized.setdefault("type", "object")
    required = normalized.get("required")
    if required is not None and not isinstance(required, list):
        normalized.pop("required", None)
    return normalized


def _build_description(server_name: str, tool_def: McpToolDef) -> str:
    prefix = f"[MCP:{server_name}]"
    title = (tool_def.title or "").strip()
    desc = (tool_def.description or "").strip()
    if title and desc:
        body = f"{title} — {desc}"
    else:
        body = title or desc or f"MCP 工具 {tool_def.name}"
    return f"{prefix} {body}"


class _McpToolBase(Func):
    """动态 MCP 工具基类（子类经 abc.ABCMeta 生成，仅覆写类属性）。"""

    name: Optional[str] = None
    mcp_server: str = ""
    mcp_tool: str = ""
    _mcp_description: str = ""
    _mcp_input_schema: dict = {}

    def __init__(self, arguments: Optional[dict] = None):
        super().__init__()
        self.arguments = dict(arguments or {})

    @classmethod
    def to_tool_schema(cls):
        return {
            "type": "function",
            "function": {
                "name": cls.name,
                "description": cls._mcp_description,
                "parameters": cls._mcp_input_schema,
            },
        }

    @classmethod
    def from_args(cls, args):
        """直接透传参数对象（MCP 参数由服务端 schema 定义，不按签名取值）。"""
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (json.JSONDecodeError, TypeError, ValueError):
                args = {}
        return cls(args if isinstance(args, dict) else {})

    @classmethod
    def display_params(cls, arguments: dict, max_len: int = 80) -> str:
        if not isinstance(arguments, dict) or not arguments:
            return ""
        parts: list = []
        for key, value in arguments.items():
            text = str(value)
            if len(text) > 40:
                text = text[:37] + "..."
            parts.append(f"{key}={text}")
        result = " ".join(parts)
        if len(result) > max_len:
            result = result[: max_len - 3] + "..."
        return result

    async def execute(self) -> str:
        from ._runtime import call_tool

        result = await call_tool(
            self.mcp_server, self.mcp_tool, self.arguments,
        )
        blocks = result.content_blocks()
        if blocks and _model_supports_images(self):
            self.result_blocks = [{"type": "text", "text": result.text}] + blocks
        text = result.text or f"(MCP 工具 {self.mcp_tool} 无输出)"
        if result.is_error and not text.startswith("("):
            # 与内置工具失败约定对齐（`(` 前缀 → 展示层标记失败）
            text = f"(MCP 工具执行失败: {text})"
        return text


def _model_supports_images(tool) -> bool:
    """当前 Agent 模型是否支持多模态（决定是否附图片 blocks）。"""
    try:
        from ..api.multimodal import is_multimodal_model
        model = getattr(getattr(tool, "agent", None), "model", None)
        return bool(is_multimodal_model(model))
    except Exception:
        _logger.debug("MCP 工具多模态判定失败（按非多模态处理）", exc_info=True)
        return False


def build_mcp_tool_class(
    server_name: str,
    tool_def: McpToolDef,
    parallel_safe: bool = False,
    disambiguate: bool = False,
) -> type:
    """为一个 MCP 工具生成 Func 子类。

    Args:
        server_name: MCP 服务器名。
        tool_def: 服务端工具定义。
        parallel_safe: 是否声明并行安全（影响 DAG 调度）。
        disambiguate: 工具名经清洗后与他人碰撞时置 True，强制加哈希后缀。
    """
    tool_name = mcp_tool_name(server_name, tool_def.name, disambiguate=disambiguate)
    schema = normalize_input_schema(tool_def.input_schema)
    description = _build_description(server_name, tool_def)

    namespace = {
        "name": tool_name,
        "mcp_server": server_name,
        "mcp_tool": tool_def.name,
        "_mcp_description": description,
        "_mcp_input_schema": schema,
        "__doc__": description,
    }
    cls_name = "McpTool_" + sanitize_identifier(tool_name)
    cls = abc.ABCMeta(cls_name, (_McpToolBase,), namespace)
    setattr(cls, "_tool_metadata", ToolMetadata(
        parallel_safe=bool(parallel_safe),
        requires_network=True,
        requires_terminal=False,
        timeout_estimate=0,
        category="mcp",
        priority=120,
        tool_category="general",
        description=description,
    ))
    return cls


__all__ = [
    "MCP_TOOL_PREFIX",
    "sanitize_identifier",
    "mcp_tool_name",
    "is_mcp_tool",
    "normalize_input_schema",
    "build_mcp_tool_class",
]
