"""ToolRegistry 适配器 — 包装 ToolRegistry 实例"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Type

from ..ports.tools import ToolRegistryPort

if TYPE_CHECKING:
    from ...tools.base import ToolMetadata


class _ToolRegistryAdapter:
    """工具注册表适配器 — 包装注册表实例，提供与 ToolRegistryPort 兼容的接口"""

    def __init__(self, registry: ToolRegistryPort):
        self._registry = registry

    def get_schemas(self) -> list[dict]:
        return self._registry.get_schemas()

    def dispatch(self, tool_name: str, arguments: dict, agent=None):
        return self._registry.dispatch(tool_name, arguments, agent)

    def build_system_prompt(self) -> list[str]:
        return self._registry.build_system_prompt()

    def get_tools(self) -> dict[str, Type]:
        return self._registry.get_tools()

    def get_metadata(self, tool_name: str) -> Optional[ToolMetadata]:
        return self._registry.get_metadata(tool_name)
