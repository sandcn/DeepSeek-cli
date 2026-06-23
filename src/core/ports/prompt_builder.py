"""提示词构建端口 — 核心层与提示词构建系统的接口"""
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
    def build_plan_agent_prompt(self, cwd: Optional[str] = None) -> list[str]:
        ...

    @abstractmethod
    def build_read_memory_agent_system_prompt(self, cwd: Optional[str] = None) -> list[str]:
        ...

    @abstractmethod
    def build_write_memory_agent_system_prompt(self, cwd: Optional[str] = None) -> list[str]:
        ...

    @abstractmethod
    def build_plan_execute_agent_system_prompt(self, cwd: Optional[str] = None) -> list[str]:
        ...


class DefaultPromptBuilderAdapter(PromptBuilderPort):
    def build_system_prompt(self) -> list[str]:
        from ...prompt_builder import build_system_prompt
        return build_system_prompt()

    def build_subagent_prompt(self, cwd: Optional[str] = None) -> list[str]:
        from ...prompt_builder import build_subagent_system_prompt
        return build_subagent_system_prompt(cwd=cwd)

    def build_map_agent_prompt(self, cwd: Optional[str] = None) -> list[str]:
        from ...prompt_builder import build_map_agent_system_prompt
        return build_map_agent_system_prompt(cwd=cwd)

    def build_review_agent_prompt(self, cwd: Optional[str] = None) -> list[str]:
        from ...prompt_builder import build_review_agent_system_prompt
        return build_review_agent_system_prompt(cwd=cwd)

    def build_plan_agent_prompt(self, cwd: Optional[str] = None) -> list[str]:
        from ...prompt_builder import build_plan_agent_system_prompt
        return build_plan_agent_system_prompt(cwd=cwd)

    def build_read_memory_agent_system_prompt(self, cwd: Optional[str] = None) -> list[str]:
        from ...prompt_builder import build_read_memory_agent_system_prompt
        return build_read_memory_agent_system_prompt(cwd=cwd)

    def build_write_memory_agent_system_prompt(self, cwd: Optional[str] = None) -> list[str]:
        from ...prompt_builder import build_write_memory_agent_system_prompt
        return build_write_memory_agent_system_prompt(cwd=cwd)

    def build_plan_execute_agent_system_prompt(self, cwd: Optional[str] = None) -> list[str]:
        from ...prompt_builder import build_plan_execute_agent_system_prompt
        return build_plan_execute_agent_system_prompt(cwd=cwd)


