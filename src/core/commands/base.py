"""命令插件系统 — 插件化命令基类 + 注册表

在现有命令注册系统的基础上增加：
- 类式命令（继承 CommandPlugin）
- 生命周期钩子（on_register, on_unregister）
- 元数据（别名、分组、描述、用法）
- 自动发现
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

_logger = logging.getLogger(__name__)


@dataclass
class CommandMeta:
    """命令元数据"""
    name: str
    aliases: list[str] = field(default_factory=list)
    group: str = "general"
    description: str = ""
    usage: str = ""
    hidden: bool = False


class CommandPlugin(ABC):
    """命令插件基类

    继承此类实现插件化命令。
    使用 @command_plugin 装饰器注册。

    使用方式:
        @command_plugin(
            name="hello",
            aliases=["hi"],
            group="general",
            description="打招呼"
        )
        class HelloCommand(CommandPlugin):
            def execute(self, ctx: CommandContext) -> bool:
                self.output("你好！")
                return True
    """

    meta: CommandMeta

    def __init__(self):
        if not hasattr(self, 'meta'):
            self.meta = CommandMeta(name=self.__class__.__name__.lower())

    @abstractmethod
    def execute(self, ctx: Any) -> bool:
        """执行命令

        Args:
            ctx: 命令执行上下文（CommandContext 实例）

        Returns:
            True 表示命令已处理，False 表示未处理
        """
        ...

    def on_register(self) -> None:
        """注册时的回调"""
        pass

    def on_unregister(self) -> None:
        """注销时的回调"""
        pass

    def output(self, text: str) -> None:
        """便捷输出方法"""
        from ...core.adapters.output import get_default_output_port
        port = get_default_output_port()
        port.write(text)

    def help_text(self) -> str:
        """生成帮助文本"""
        parts = []
        parts.append(f"/{self.meta.name}")
        if self.meta.usage:
            parts.append(f" {self.meta.usage}")
        parts.append(f"  — {self.meta.description}")
        if self.meta.aliases:
            parts.append(f" (别名: {', '.join('/' + a for a in self.meta.aliases)})")
        return "".join(parts)


# ── 插件注册表 ────────────────────────────────────────

class CommandPluginRegistry:
    """命令插件注册表

    管理所有 CommandPlugin 的注册和发现。
    """

    def __init__(self):
        self._plugins: dict[str, CommandPlugin] = {}  # name → plugin
        self._alias_map: dict[str, str] = {}  # alias → name
        self._groups: dict[str, list[str]] = {}  # group → [names]
        self._interactive_plugins: dict[str, InteractiveCommandPlugin] = {}  # InteractiveCommandPlugin: name → plugin
        #: 曾注册过的插件快照（注册不清除）——命令服务卸载注销后，
        #: 全局注册表可据此按需恢复，避免「模块已导入但注册表被清空」。
        self._known_plugins: dict[str, CommandPlugin] = {}

    def register(self, plugin: CommandPlugin) -> None:
        """注册一个命令插件"""
        name = plugin.meta.name

        if name in self._plugins:
            _logger.warning("命令已存在，覆盖: %s", name)

        self._plugins[name] = plugin
        self._known_plugins[name] = plugin
        self._alias_map[name] = name

        # 注册别名
        for alias in plugin.meta.aliases:
            self._alias_map[alias] = name

        # 分组
        group = plugin.meta.group
        if group not in self._groups:
            self._groups[group] = []
        if name not in self._groups[group]:
            self._groups[group].append(name)

        # 同时注册到旧的命令系统（保持向后兼容）
        from ..internal.commands._command_core import register_command
        register_command(
            f"/{name}",
            lambda ctx, p=plugin: p.execute(ctx),
            help_text=plugin.meta.description,
        )
        for alias in plugin.meta.aliases:
            register_command(
                f"/{alias}",
                lambda ctx, p=plugin: p.execute(ctx),
                help_text=f"别名: /{name}",
            )

        plugin.on_register()

        # 检测是否为 InteractiveCommandPlugin 子类，若是则额外存入交互式插件字典
        if hasattr(plugin, 'bind_loop'):
            self._interactive_plugins[name] = plugin

        _logger.debug("命令插件已注册: %s", name)

    def register_interactive(self, plugin: InteractiveCommandPlugin) -> None:
        """注册交互式命令插件（委托 register 处理）"""
        self.register(plugin)

    def restore(self) -> int:
        """按已知插件快照重建活跃注册（命令服务卸载清空后按需恢复）。

        命令插件在模块导入时经装饰器注册；命令服务卸载会注销全部内置命令。
        若此后同一进程仍要访问命令能力（自省、单元测试隔离），注册表可按
        快照恢复，无需重新导入模块。返回恢复的命令数。
        """
        restored = 0
        for name, plugin in self._known_plugins.items():
            if name not in self._plugins:
                self.register(plugin)
                restored += 1
        return restored

    def bind_loop(self, loop: Any) -> None:
        """遍历所有已注册的 interactive plugin 调用 plugin.bind_loop(loop)

        在 InteractiveLoop 初始化完成后调用，为所有交互式插件注入 loop 引用。
        """
        for plugin in self._interactive_plugins.values():
            plugin.bind_loop(loop)

    def unregister(self, name: str) -> bool:
        """注销一个命令插件

        Returns:
            成功返回 True，未找到返回 False
        """
        if name not in self._plugins:
            return False

        plugin = self._plugins[name]
        del self._plugins[name]

        # 清理别名
        for alias in list(self._alias_map.keys()):
            if self._alias_map[alias] == name:
                del self._alias_map[alias]

        # 清理分组
        for group in self._groups:
            self._groups[group] = [n for n in self._groups[group] if n != name]

        plugin.on_unregister()
        return True

    def get(self, name: str) -> Optional[CommandPlugin]:
        """通过名称或别名获取插件（自动去除 / 前缀）"""
        name = name.lstrip("/")
        real_name = self._alias_map.get(name)
        if real_name is None:
            return None
        return self._plugins.get(real_name)

    def list(self, group: Optional[str] = None) -> list[CommandPlugin]:
        """列出所有插件（可选按分组过滤）"""
        if group:
            return [
                self._plugins[n] for n in self._groups.get(group, [])
                if n in self._plugins
            ]
        return list(self._plugins.values())

    def list_groups(self) -> list[str]:
        """列出所有分组"""
        return list(self._groups.keys())

    def exists(self, name: str) -> bool:
        """检查命令是否存在"""
        return name in self._alias_map

    def count(self) -> int:
        return len(self._plugins)

    def clear(self) -> None:
        """清空所有插件"""
        self._plugins.clear()
        self._alias_map.clear()
        self._groups.clear()


# ── 装饰器 ────────────────────────────────────────────

_command_plugins = CommandPluginRegistry()


def command_plugin(
    name: Optional[str] = None,
    aliases: Optional[list[str]] = None,
    group: str = "general",
    description: str = "",
    usage: str = "",
    hidden: bool = False,
):
    """命令插件装饰器

    使用方式:
        @command_plugin(name="hello", description="打招呼")
        class HelloCommand(CommandPlugin):
            def execute(self, ctx):
                return True
    """
    def decorator(cls):
        if not issubclass(cls, CommandPlugin):
            raise TypeError(f"{cls.__name__} 必须继承 CommandPlugin")

        meta_name = name or cls.__name__.lower()
        if meta_name.endswith("command"):
            meta_name = meta_name[:-7]

        cls.meta = CommandMeta(
            name=meta_name,
            aliases=aliases or [],
            group=group,
            description=description,
            usage=usage,
            hidden=hidden,
        )
        _command_plugins.register(cls())
        return cls
    return decorator


def get_plugin_registry() -> CommandPluginRegistry:
    """获取命令插件注册表（内核 ``ctx.commands`` 优先，回退全局单例）。

    「一切皆插件」：内核挂载命令插件后，命令能力经内核服务解析；内核缺失
    （单元测试、独立调用）或命令插件尚在构造中时回退全局单例。两者在正常
    运行时指向同一实例（命令插件包装的正是该单例）。
    """
    try:
        from ...kernel.runtime import active_service

        service = active_service("commands")
        registry = getattr(service, "registry", None) if service is not None else None
        if registry is not None:
            return registry
    except Exception:
        pass
    # 命令服务卸载会注销内置命令（可逆副作用），但命令插件模块已导入——
    # 全局注册表为空时按已知快照恢复，避免后续访问拿到空注册表。
    if not _command_plugins._plugins and _command_plugins._known_plugins:
        _command_plugins.restore()
    return _command_plugins
