"""动态工具定义 — 让插件在运行期注册模型可调用的工具。

对应 dsh 的 ``defineTool``：插件（含运行时自修改插件）可以声明一个名字、
JSON Schema 与异步处理函数，得到一个标准 ``Func`` 子类，再经
``ctx.tools.register`` 注册进工具注册表，与内置工具同构调度。
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable, Optional, Type

from .base import Func, ToolMetadata, tool_metadata

_logger = logging.getLogger(__name__)


def define_tool(
    name: str,
    schema: dict,
    handler: Callable[..., Awaitable[object]],
    *,
    description: str = "",
    parallel_safe: bool = False,
    requires_network: bool = False,
    requires_terminal: bool = False,
    timeout_estimate: float = 0,
    category: str = "general",
    tool_category: str = "general",
) -> Type[Func]:
    """从 schema + handler 构造一个 ``Func`` 子类。

    Args:
        name: 工具名（唯一）。
        schema: 完整 OpenAI function schema（``{"type": "function", "function": {...}}``）；
            若传入的是 ``function`` 内层字典（含 name/description/parameters），
            自动包一层。
        handler: 异步处理函数，按 schema 参数以关键字调用（``await handler(**args)``）。
        description: 元数据描述（缺省取 schema 内的 description）。

    Returns:
        ``Func`` 子类（未注册，需 ``ctx.tools.register``）。
    """
    if not isinstance(name, str) or not name:
        raise ValueError(f"工具名必须是非空字符串: {name!r}")
    if not callable(handler):
        raise TypeError(f"handler 必须可调用: {handler!r}")

    full_schema = _normalize_schema(name, schema, description)

    class _DynamicTool(Func):
        pass

    _DynamicTool.__name__ = "".join(part.capitalize() for part in name.split("_")) or "DynamicTool"
    _DynamicTool.__qualname__ = _DynamicTool.__name__
    _DynamicTool.name = name
    _DynamicTool._dynamic_schema = full_schema
    _DynamicTool._dynamic_handler = handler

    @classmethod
    def to_tool_schema(cls):
        return cls._dynamic_schema

    @classmethod
    def from_args(cls, args: dict):
        instance = cls()
        instance._dynamic_args = dict(args or {})
        return instance

    async def execute(self) -> object:
        args = getattr(self, "_dynamic_args", {}) or {}
        handler = type(self)._dynamic_handler
        result = handler(**args)
        if hasattr(result, "__await__"):
            result = await result
        return "" if result is None else result

    _DynamicTool.to_tool_schema = to_tool_schema
    _DynamicTool.from_args = from_args
    _DynamicTool.execute = execute
    # 运行期补齐实现后清除抽象方法缓存（否则实例化被 ABC 拒绝）
    _DynamicTool.__abstractmethods__ = frozenset()

    meta_desc = description or (full_schema.get("function", {}) or {}).get("description", "")
    tool_metadata(
        parallel_safe=parallel_safe,
        requires_network=requires_network,
        requires_terminal=requires_terminal,
        timeout_estimate=timeout_estimate,
        category=category,
        tool_category=tool_category,
        description=meta_desc,
    )(_DynamicTool)

    return _DynamicTool


def _normalize_schema(name: str, schema: dict, description: str) -> dict:
    if not isinstance(schema, dict):
        raise TypeError(f"schema 必须是字典: {schema!r}")
    if schema.get("type") == "function" and "function" in schema:
        return schema
    function = dict(schema)
    function.setdefault("name", name)
    if description:
        function.setdefault("description", description)
    function.setdefault("parameters", {"type": "object", "properties": {}})
    return {"type": "function", "function": function}


__all__ = ["define_tool"]
