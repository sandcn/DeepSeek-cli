"""AgentBuilder — Agent 建造者（链式 API）

使用方式:
    agent = (AgentBuilder()
        .model("deepseek-v4-pro")
        .with_display_port(my_display)
        .with_config_port(my_config)
        .build())
"""

from __future__ import annotations

from typing import Optional

from .agent import Agent


class AgentBuilder:
    """Agent 建造者 — 链式 API 简化 Agent 创建。

    默认值:
    - registry: ToolRegistry.default()
    - sandbox: get_sandbox_manager()
    - all ports: Default*Adapter
    """

    def __init__(self):
        self._model: Optional[str] = None
        self._registry = None
        self._sandbox = None
        self._display_port = None
        self._event_port = None
        self._output_port = None
        self._config_port = None
        self._async_model_port = None
        self._prompt_builder_port = None
        self._observability_port = None

    def model(self, model: str) -> AgentBuilder:
        self._model = model
        return self

    def with_registry(self, registry) -> AgentBuilder:
        self._registry = registry
        return self

    def with_sandbox(self, sandbox) -> AgentBuilder:
        self._sandbox = sandbox
        return self

    def with_display_port(self, port) -> AgentBuilder:
        self._display_port = port
        return self

    def with_event_port(self, port) -> AgentBuilder:
        self._event_port = port
        return self

    def with_output_port(self, port) -> AgentBuilder:
        self._output_port = port
        return self

    def with_config_port(self, port) -> AgentBuilder:
        self._config_port = port
        return self

    def with_async_model_port(self, port) -> AgentBuilder:
        self._async_model_port = port
        return self

    def with_prompt_builder_port(self, port) -> AgentBuilder:
        self._prompt_builder_port = port
        return self

    def with_observability_port(self, port) -> AgentBuilder:
        self._observability_port = port
        return self

    def build(self) -> Agent:
        """构建 Agent 实例。

        所有未设置的可选参数使用 Agent.__init__ 的默认值。
        """
        return Agent(
            model=self._model,
            registry=self._registry,
            sandbox=self._sandbox,
            display_port=self._display_port,
            event_port=self._event_port,
            output_port=self._output_port,
            config_port=self._config_port,
            async_model_port=self._async_model_port,
            prompt_builder_port=self._prompt_builder_port,
            observability_port=self._observability_port,
        )


__all__ = ["AgentBuilder"]
