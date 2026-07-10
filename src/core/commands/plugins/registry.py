"""交互式命令注册表 — 管理与 InteractiveLoop 绑定的命令插件

与 CommandPluginRegistry 的区别：
- 不注册到旧式命令系统（不调用 register_command）
- 不处理别名映射
- 插件实例绑定 InteractiveLoop 实例（通过 bind_loop）
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .base import InteractiveCommandPlugin

_logger = logging.getLogger(__name__)


class InteractiveCommandRegistry:
    """交互式命令插件注册表

    管理需要 InteractiveLoop 内部状态的命令插件。
    每个插件通过 register() 注册，通过 bind_loop() 绑定 loop 实例。
    """

    def __init__(self):
        self._plugins: dict[str, InteractiveCommandPlugin] = {}

    def register(self, plugin: InteractiveCommandPlugin) -> None:
        """注册一个交互命令插件"""
        name = plugin.name
        if name in self._plugins:
            _logger.warning("交互命令已存在，覆盖: %s", name)
        self._plugins[name] = plugin
        _logger.debug("交互命令已注册: %s", name)

    def get(self, cmd_name: str) -> Optional[InteractiveCommandPlugin]:
        """通过命令名获取插件（自动去除 / 前缀）"""
        name = cmd_name.lstrip("/")
        return self._plugins.get(name)

    def unregister(self, name: str) -> bool:
        """注销一个命令插件"""
        name = name.lstrip("/")
        if name in self._plugins:
            del self._plugins[name]
            return True
        return False

    def list(self) -> list[InteractiveCommandPlugin]:
        """列出所有注册的插件"""
        return list(self._plugins.values())

    def clear(self) -> None:
        """清空所有插件"""
        self._plugins.clear()

    def count(self) -> int:
        return len(self._plugins)


# 全局单例
_interactive_registry = InteractiveCommandRegistry()


def get_interactive_registry() -> InteractiveCommandRegistry:
    """获取全局交互命令注册表"""
    return _interactive_registry
