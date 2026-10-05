"""命令插件 — 从清单配置声明单个命令（外部插件入口）。

「一切皆插件」：每个内置命令由清单中的独立条目声明，条目形如::

    - id: cmd_clear
      plugin: src.plugins.command_plugin
      config:
        command: src.core.commands._session_cmd.ClearCommand   # CommandPlugin 子类
        name: clear                                             # 命令名（供组合根收集）

插件挂载时导入该子类（触发其模块声明自身命令），把声明的实例注册进
``ctx.commands``；卸载时注销（可逆副作用）。因此单个命令可被 Profile/Patch/
Overlay 禁用、覆盖或替换，而无需改动命令服务源码。
"""

from __future__ import annotations

from ..kernel import plugin


def import_attr(dotted: str):
    """导入点分路径指向的对象（``pkg.mod.Class`` / ``pkg.mod:attr``）。"""
    from .tool_plugin import import_attr as _import_attr

    return _import_attr(dotted)


@plugin("command", inject=["commands"])
def apply(ctx):
    ref = ctx.config.get("command")
    if not ref:
        return
    from ..core.commands.base import (
        CommandPlugin,
        declare_command_plugin,
        declared_command_plugin,
    )

    cls = import_attr(ref)
    if not (isinstance(cls, type) and issubclass(cls, CommandPlugin)):
        raise TypeError(f"不是 CommandPlugin 子类: {ref!r}")

    name = ctx.config.get("name")
    instance = declared_command_plugin(name) if name else None
    if instance is None:
        instance = cls()
        declare_command_plugin(instance)
        if not name:
            name = instance.meta.name

    registry = ctx.commands.registry
    if registry.get(name) is None:
        registry.register(instance)
    ctx.effect(lambda: (lambda: registry.unregister(name)))
