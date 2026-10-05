"""MCP 传输条目插件 — 清单中每个内置 MCP 传输一个独立插件条目。

「一切皆插件」：内置 MCP 传输（stdio/http/sse）不再由
``src/mcp/transport.py::create_transport`` 的 ``if/elif`` 硬编码分支路由，而是由
清单中的独立条目声明::

    - id: mcp_transport_stdio
      plugin: src.plugins.mcp_transports:apply_mcp_transport
      config:
        name: stdio                       # 内置传输 id（可被 patch/overlay 定位）
        # transport: my_pkg.MyTransport   # 可选：替换实现（点分路径）

插件挂载时把该传输注册进 MCP 传输注册表（``factory=None`` 用默认实现）；卸载时
撤销（可逆副作用）。条目被 Patch/Overlay 禁用即不挂载，对应内置传输随之缺席
（``mcp`` 聚合插件经 ``managed_mcp_transports`` 抑制默认装配）。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from ..kernel import plugin


def _make_override_factory(config: dict) -> Optional[Callable[[Any], Any]]:
    ref = config.get("transport")
    if not ref:
        return None

    def _factory(cfg):
        from .tool_plugin import import_attr

        return import_attr(ref)(cfg)

    _factory.__name__ = f"_override_{ref}"
    return _factory


@plugin("mcp_transport")
def apply_mcp_transport(ctx):
    from ..mcp.transport_registry import register_builtin_mcp_transport

    name = ctx.config.get("name")
    if not name:
        raise ValueError("mcp_transport 条目缺少 config.name")
    undo = register_builtin_mcp_transport(name, _make_override_factory(ctx.config))
    ctx.effect(lambda: undo)


__all__ = ["apply_mcp_transport"]
