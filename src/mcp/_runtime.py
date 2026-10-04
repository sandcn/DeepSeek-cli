"""MCP 工具调用钩子 — 依赖倒置入口

``mcp/tool.py`` 的 MCP 工具 ``execute()`` 需要调用 McpManager，但
``mcp/manager.py`` 依赖 ``tool.py``（构建工具类）——直接互引会形成循环。
本模块提供注册式钩子解耦：

    - mcp.manager 在导入时 ``register_caller(...)`` 注册调用实现；
    - mcp.tool 经 ``call_tool(...)`` 转发；
    - 未注册时抛 RuntimeError（MCP 工具执行前 manager 必然已导入）。

依赖方向：mcp.tool → mcp._runtime ← mcp.manager。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

_caller: Optional[Callable] = None


def register_caller(fn: Optional[Callable]) -> None:
    """注册 MCP 工具调用实现（None 表示注销）。"""
    global _caller
    _caller = fn


async def call_tool(server: str, tool: str, arguments: Optional[dict] = None) -> Any:
    """转发 MCP 工具调用（经已注册实现）。"""
    caller = _caller
    if caller is None:
        raise RuntimeError("MCP 调用器未注册：请先导入 mcp.manager")
    return await caller(server, tool, arguments)


__all__ = ["register_caller", "call_tool"]
