"""命令插件 — 提供 ``ctx.commands``。

命令系统的插件注册表（``CommandPluginRegistry``）本身就管理一批命令插件；
本插件负责确保内置命令插件已加载，并把注册表作为服务暴露；卸载时注销
本插件注册的命令（可逆副作用）。
"""

from __future__ import annotations

from ..kernel import Service, plugin

# 内置命令插件的名 → 实例缓存（模块级，确保卸载后可重新注册）
_BUILTIN_COMMAND_CACHE: dict | None = None


def _ensure_builtin_commands():
    """导入内置命令模块；首次快照，后续按需补注册。"""
    global _BUILTIN_COMMAND_CACHE
    from ..core.commands.base import get_plugin_registry

    registry = get_plugin_registry()
    # 触发命令模块导入（导入副作用完成注册）
    import importlib

    importlib.import_module("src.core.commands")
    importlib.import_module("src.core.commands.plugins")

    if _BUILTIN_COMMAND_CACHE is None:
        _BUILTIN_COMMAND_CACHE = dict(registry._plugins)
    else:
        for name, command in _BUILTIN_COMMAND_CACHE.items():
            if name not in registry._plugins:
                registry.register(command)
    return registry


class CommandService(Service):
    """命令服务 — 占据 ``ctx.commands``。"""

    provide = "commands"
    name = "commands"
    inject = ("config",)

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        self._registry = _ensure_builtin_commands()
        self._names = list(self._registry._plugins)
        ctx.effect(lambda: self._unregister_all)

    def _unregister_all(self) -> None:
        for name in self._names:
            self._registry.unregister(name)

    @property
    def registry(self):
        return self._registry

    def get(self, name: str):
        return self._registry.get(name)

    def list(self):
        return self._registry.list()

    def register(self, command) -> None:
        self._registry.register(command)

    def unregister(self, name: str) -> bool:
        return self._registry.unregister(name)

    def count(self) -> int:
        return self._registry.count()


@plugin("commands", inject=["config"], provide=["commands"])
def apply(ctx):
    return CommandService(ctx)
