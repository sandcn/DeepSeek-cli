"""交互式命令注册表 — 向后兼容存根

InteractiveCommandRegistry 已合并到 CommandPluginRegistry（commands/base.py）。
此模块保留 get_interactive_registry() 和 _interactive_registry 作为向后兼容导出。
"""

from __future__ import annotations

import logging

from ..base import get_plugin_registry, CommandPluginRegistry

_logger = logging.getLogger(__name__)


# 向后兼容：_interactive_registry 指向统一的 CommandPluginRegistry 实例
_interactive_registry: CommandPluginRegistry = get_plugin_registry()


def get_interactive_registry() -> CommandPluginRegistry:
    """向后兼容 — 返回统一的命令插件注册表实例

    原 InteractiveCommandRegistry 已合并到 CommandPluginRegistry，
    此函数返回相同的全局实例。
    """
    return _interactive_registry
