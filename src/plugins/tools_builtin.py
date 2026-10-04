"""内置工具插件 — 以插件方式显式注册全部内置工具。

「一切皆插件」：内置工具不再是注册表的隐式自动发现副作用，而是由本插件在
挂载时经 ``ctx.tools.register`` 逐个注册（每个注册都是可逆副作用，卸载时随
``tools`` 服务清空）。注册前先把注册表标记为已初始化，避免懒发现重复注册。
"""

from __future__ import annotations

from ..kernel import plugin


@plugin("tools_builtin", inject=["tools"])
def apply(ctx):
    from ..tools.registry import discover_builtin_tools

    service = ctx.tools
    service.mark_initialized()
    for name, tool_class in discover_builtin_tools().items():
        if service.get(name) is None:
            service.register(tool_class)
