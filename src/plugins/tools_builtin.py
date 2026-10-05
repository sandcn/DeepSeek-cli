"""内置工具插件 — 兜底注册未由清单接管的工具。

「一切皆插件」：每个内置工具默认由清单中的独立条目（``tools`` bundle 的
``tool_*`` 条目，经 ``src.plugins.tool_plugin``）显式注册——因此单个工具可被
Profile/Bundle 声明、被 Patch/Overlay 按 id 禁用或替换。

本插件只做**兜底**：注册那些没有出现在清单里的内置工具（例如第三方新加进
``src/tools`` 的模块、或在无清单的独立调用/单元测试场景）。已由清单接管的
工具名（含被禁用的）经 ``config.managed_tools`` 注入，本插件跳过它们——因此
overlay 禁用一个工具后不会被这里重新注册。
"""

from __future__ import annotations

from ..kernel import plugin


@plugin("tools_builtin", inject=["tools"])
def apply(ctx):
    from ..tools.registry import discover_builtin_tools

    service = ctx.tools
    managed = set(ctx.config.get("managed_tools") or ())
    service.mark_initialized()
    for name, tool_class in discover_builtin_tools().items():
        if name in managed:
            continue
        if service.get(name) is None:
            service.register(tool_class)
