"""Agent 循环插件 — 提供 ``ctx.agent_loop``。

Agent 循环本身也是插件：负责按内核服务组装 Agent（工具注册表、模型端口、
配置端口、提示词端口、显示端口），并驱动一轮对话。
"""

from __future__ import annotations

from ..kernel import Service, plugin


def _event_ports():
    from ..core.adapters.display import DefaultDisplayAdapter
    from ..core.adapters.events import DisplayEventBusAdapter
    from ..core.adapters.output import DefaultOutputAdapter

    return (
        DefaultDisplayAdapter(source="agent"),
        DisplayEventBusAdapter(source="agent"),
        DefaultOutputAdapter(),
    )


class AgentLoopService(Service):
    """Agent 循环服务 — 占据 ``ctx.agent_loop``。"""

    provide = "agent_loop"
    name = "agent_loop"
    inject = ("tools", "llm", "config", "prompt", "events", "presets")

    def create_agent(
        self,
        *,
        model=None,
        with_event_ports: bool = True,
        null_ports: bool = False,
        preset: str = "",
        kind: str = "main",
        session_id: str = None,
        parent=None,
        isolated=None,
        meta=None,
        **extra,
    ):
        """按内核服务组装 Agent。

        - ``with_event_ports=True``（默认）：注入事件化显示/输出端口
          （DisplayEventBus → ChatUIConsumer 渲染）；
        - ``null_ports=True``：注入 NullPort（无 UI 依赖，供 ChatSession 默认）；
        - 两者皆否：使用 Agent 自身默认端口。

        组装完成后把 Agent 登记到 ``ctx.agents`` 活跃注册表（存在该服务时），
        使其出现在 Agent 事件域并拥有独立作用域。
        """
        from ..core.agent import Agent

        kwargs = dict(extra)
        kwargs["registry"] = kwargs.pop("registry", self.ctx.tools.registry)
        kwargs.setdefault("config_port", self.ctx.consume("config").port)
        kwargs.setdefault("async_model_port", self.ctx.llm.model_port())
        kwargs.setdefault("prompt_builder_port", self.ctx.prompt.port)
        # 可观测性端口（内核 observability 服务优先；缺失回退 Agent 默认适配器）
        if self.ctx.has("observability"):
            observability_port = self.ctx.consume("observability").port()
            if observability_port is not None:
                kwargs.setdefault("observability_port", observability_port)
        if null_ports:
            from ..core.adapters.null import (
                _NullDisplayPort,
                _NullEventPort,
                _NullOutputPort,
            )

            kwargs.setdefault("display_port", _NullDisplayPort())
            kwargs.setdefault("event_port", _NullEventPort())
            kwargs.setdefault("output_port", _NullOutputPort())
        elif with_event_ports:
            display_port, event_port, output_port = _event_ports()
            kwargs.setdefault("display_port", display_port)
            kwargs.setdefault("event_port", event_port)
            kwargs.setdefault("output_port", output_port)
        agent = Agent(model=model, **kwargs)
        if preset:
            self.ctx.consume("presets").apply_to_agent(preset, agent)
        self.register(agent, kind=kind, session_id=session_id, parent=parent,
                      isolated=isolated, meta=meta)
        return agent

    def register(self, agent, *, kind: str = "main", session_id=None,
                 parent=None, isolated=None, meta=None):
        """把 Agent 登记到活跃注册表（无 agents 服务时静默跳过）。"""
        if not self.ctx.has("agents"):
            return None
        try:
            return self.ctx.consume("agents").register(
                agent,
                kind=kind,
                session_id=session_id,
                parent=parent,
                isolated=isolated,
                meta=meta,
                model=getattr(agent, "model", ""),
            )
        except Exception:
            import logging

            logging.getLogger(__name__).debug("agent_loop 登记 agent 失败", exc_info=True)
            return None

    def unregister(self, agent) -> bool:
        """从活跃注册表注销 Agent。"""
        if not self.ctx.has("agents"):
            return False
        return self.ctx.consume("agents").unregister(agent)

    def active_agents(self):
        """活跃 Agent 记录列表（无 agents 服务时为空）。"""
        if not self.ctx.has("agents"):
            return []
        return self.ctx.consume("agents").active()

    def make_event_agent(self, model=None):
        """事件化 Agent（显示/工具调用经 DisplayEventBus 渲染到 TUI）。"""
        return self.create_agent(model=model)

    def create_headless_agent(self, *, model=None, observability_port=None, sandbox=None):
        """无 UI Agent（NullPort），供 ChatSession 默认构造。"""
        kwargs = {}
        if observability_port is not None:
            kwargs["observability_port"] = observability_port
        if sandbox is not None:
            kwargs["sandbox"] = sandbox
        return self.create_agent(model=model, null_ports=True, **kwargs)

    async def run(self, agent) -> bool:
        return await agent.run()

    def build_system_prompt(self) -> list[str]:
        return self.ctx.prompt.build()


def make_event_agent(model=None):
    """组合根辅助：优先使用内核 agent_loop 服务，否则回退默认 Agent。"""
    from ..kernel import get_current_kernel

    kernel = get_current_kernel()
    if kernel is not None and kernel.has_service("agent_loop"):
        return kernel.resolve_service("agent_loop").make_event_agent(model=model)
    display_port, event_port, output_port = _event_ports()
    from ..core.agent import Agent

    return Agent(
        model=model,
        display_port=display_port,
        event_port=event_port,
        output_port=output_port,
    )


@plugin("agent_loop", inject=["tools", "llm", "config", "prompt", "events"], provide=["agent_loop"])
def apply(ctx):
    return AgentLoopService(ctx)
