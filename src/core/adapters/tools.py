"""工具注册表适配器 — 依赖倒置工厂实现（桥接工具系统）。

核心层经 ``core.ports.tools.ToolRegistryPort`` 协议访问工具注册表；本模块
提供默认实现，负责延迟导入基础设施层（``tools.registry``）。适配器层
允许依赖基础设施层（桥接职责）。
"""

from __future__ import annotations


def get_default_tool_registry():
    """返回默认工具注册表实例（``ToolRegistry.default()`` 进程级单例）。"""
    from ...tools.registry import ToolRegistry
    return ToolRegistry.default()


__all__ = ["get_default_tool_registry"]
