"""内置插件包 — 把项目的每一个子系统暴露为内核插件。

「一切皆插件」：工具、技能、MCP、模型适配器、Agent 循环、会话、命令、
事件、UI/渲染器、策略，全部坐在内核之上，通过稳定的 ``ctx.<key>`` 服务
相互发现，而非直接 import 具体实现。
"""

from __future__ import annotations

__all__ = ["builtin_tree"]


def builtin_tree():
    """构建内置 Profile/Bundle/Patch 配置树。"""
    from .manifest import build_config_tree

    return build_config_tree()
