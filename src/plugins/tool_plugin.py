"""工具插件 — 从清单配置声明单个工具（外部插件入口）。

清单条目示例::

    - id: my-tool
      plugin: src.plugins.tool_plugin
      config:
        tool: my_package.tools.MyTool      # Func 子类的点分路径

插件挂载时导入该 ``Func`` 子类并注册进 ``ctx.tools``；卸载时注销（可逆副作用）。
"""

from __future__ import annotations

import importlib

from ..kernel import plugin


def import_attr(dotted: str):
    """导入点分路径指向的对象（``pkg.mod.Class`` / ``pkg.mod:attr``）。"""
    if not isinstance(dotted, str) or not dotted:
        raise ValueError(f"工具引用必须是非空字符串: {dotted!r}")
    if ":" in dotted:
        module_name, _, attr = dotted.partition(":")
    else:
        module_name, _, attr = dotted.rpartition(".")
    if not module_name or not attr:
        raise ValueError(f"工具引用必须是 'module.Attr' 形式: {dotted!r}")
    module = importlib.import_module(module_name)
    obj = module
    for part in attr.split("."):
        obj = getattr(obj, part)
    return obj


@plugin("tool", inject=["tools"])
def apply(ctx):
    ref = ctx.config.get("tool")
    if not ref:
        return
    from ..tools.base import Func

    tool_class = import_attr(ref)
    if not (isinstance(tool_class, type) and issubclass(tool_class, Func)):
        raise TypeError(f"不是 Func 子类: {ref!r}")
    registry = ctx.tools.registry
    registry.register(tool_class)
    ctx.effect(lambda: (lambda: registry.unregister(tool_class.name)))
