"""命令插件 — 提供 ``ctx.commands``。

「一切皆插件」：每个内置命令是清单中的独立条目（``commands`` bundle 的
``cmd_*`` 条目，经 ``src.plugins.command_plugin`` 显式注册）。命令模块导入
时只**声明**自身提供的命令插件（``declare_command_plugin``），是否注册由本
服务按 Profile/Patch 决定——因此单个命令可被禁用/替换。

本服务按组合根注入的 ``config.managed_commands``（清单接管的命令名，含被
禁用的）注册声明的命令；未注入时兜底注册全部已声明命令（无清单场景）。
"""

from __future__ import annotations

from ..kernel import Service, plugin


def _ensure_builtin_commands():
    """导入内置命令模块（填充声明；注册由本服务或清单条目触发）。"""
    import importlib

    importlib.import_module("src.core.commands")
    importlib.import_module("src.core.commands.plugins")
    from ..core.commands.base import command_registry_singleton

    return command_registry_singleton()


def ensure_builtin_commands():
    """确保内置命令模块已导入（声明已填充）。

    公开入口——自省/展示方（如 /plugin 命令）按需调用，保证命令插件信息在
    任意入口下可见。
    """
    return _ensure_builtin_commands()


class CommandService(Service):
    """命令服务 — 占据 ``ctx.commands``。"""

    provide = "commands"
    name = "commands"
    inject = ("config",)

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        from ..core.commands.base import command_registry_singleton

        _ensure_builtin_commands()
        self._registry = command_registry_singleton()
        managed = (config or ctx.config or {}).get("managed_commands")
        self._names: list[str] = []
        if managed:
            from ..core.commands.base import declared_command_names

            wanted = {str(item) for item in managed}
            # 清除兜底路径可能预注册的、清单未接管的声明命令（保证 disable 生效）
            for existing in declared_command_names():
                if existing not in wanted:
                    self._registry.unregister(existing)
            for name in wanted:
                if self._registry.register_declared(name):
                    self._names.append(name)
        else:
            self._registry.register_all_declared()
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
