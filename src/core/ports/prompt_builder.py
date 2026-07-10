"""提示词构建端口 — 核心层与提示词构建系统的接口

适配器实现已移至 src.core.adapters.prompt_builder。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class PromptBuilderPort(ABC):
    @abstractmethod
    def build_system_prompt(self) -> list[str]:
        ...

    @abstractmethod
    def build_subagent_prompt(self, cwd: Optional[str] = None) -> list[str]:
        ...

    @abstractmethod
    def build_map_agent_prompt(self, cwd: Optional[str] = None) -> list[str]:
        ...

    @abstractmethod
    def build_review_agent_prompt(self, cwd: Optional[str] = None) -> list[str]:
        ...

    @abstractmethod
    def build_think_agent_system_prompt(self, cwd: Optional[str] = None) -> list[str]:
        ...

    @abstractmethod
    def build_plan_agent_prompt(self, cwd: Optional[str] = None) -> list[str]:
        ...

    @abstractmethod
    def build_read_memory_agent_system_prompt(self, cwd: Optional[str] = None) -> list[str]:
        ...

    @abstractmethod
    def build_write_memory_agent_system_prompt(self, cwd: Optional[str] = None) -> list[str]:
        ...

    @abstractmethod
    def build_execute_agent_system_prompt(self, cwd: Optional[str] = None) -> list[str]:
        ...


