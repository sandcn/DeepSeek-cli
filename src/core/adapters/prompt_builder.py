"""提示词构建适配器 — DefaultPromptBuilderAdapter"""
from __future__ import annotations

from typing import Optional


class DefaultPromptBuilderAdapter:
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

    def build_think_agent_system_prompt(self, cwd: Optional[str] = None) -> list[str]:
        from ...prompt_builder import build_think_agent_system_prompt
        return build_think_agent_system_prompt(cwd=cwd)

    def build_plan_agent_prompt(self, cwd: Optional[str] = None) -> list[str]:
        from ...prompt_builder import build_plan_agent_system_prompt
        return build_plan_agent_system_prompt(cwd=cwd)

    def build_read_memory_agent_system_prompt(self, cwd: Optional[str] = None) -> list[str]:
        from ...prompt_builder import build_read_memory_agent_system_prompt
        return build_read_memory_agent_system_prompt(cwd=cwd)

    def build_write_memory_agent_system_prompt(self, cwd: Optional[str] = None) -> list[str]:
        from ...prompt_builder import build_write_memory_agent_system_prompt
        return build_write_memory_agent_system_prompt(cwd=cwd)

    def build_execute_agent_system_prompt(self, cwd: Optional[str] = None) -> list[str]:
        from ...prompt_builder import build_execute_agent_system_prompt
        return build_execute_agent_system_prompt(cwd=cwd)
