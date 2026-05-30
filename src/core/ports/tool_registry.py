"""工具注册表端口 — 核心层与工具注册表的接口"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Type


class ToolRegistryPort(ABC):
    @abstractmethod
    def get_schemas(self) -> list[dict]:
        ...

    @abstractmethod
    def dispatch(self, tool_name: str, arguments: dict, agent=None) -> Any:
        ...

    @abstractmethod
    def build_system_prompt(self) -> list[str]:
        ...

    @abstractmethod
    def get_tools(self) -> dict[str, Type]:
        ...
