"""SubAgent 运行时插件 — 提供 ``ctx.subagents``。

「一切皆插件」：SubAgent 子系统（类型注册表 + 执行器装配）作为内核服务坐在
内核之上：

- ``ctx.subagents.run(parent, specs)``：并行执行一组子任务（默认
  :class:`ParallelExecutor`，可经 config ``executor`` 替换为自定义执行器）；
- ``ctx.subagents.spawn(parent, spec, index, display)``：按 spec 构造一个
  SubAgent（工厂可经 config ``agent_factory`` 替换）；
- ``ctx.subagents.register_type(spec)`` / ``types()`` / ``describe()``：
  SubAgent 类型注册表（每个类型是清单中的独立插件条目，见
  ``src.plugins.agent_type_entries``）；
- ``ctx.subagents.exclusions(agent_type)`` / ``build_prompt_parts(...)``：
  类型 → 工具排除集合 / 提示词（供策略与 SubAgent 构造使用）。

本聚合插件同时处理组合根注入的 ``managed_agent_types``（清单已接管的类型 id，
含被禁用的）——对应内置类型不再走默认装配；以及 config
``disabled_agent_types`` 显式禁用。二者均为挂在 Fiber 上的可逆副作用。
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, List, Optional

from ..core.agent_types import (
    AgentTypeSpec,
    active_specs,
    agent_type_names,
    build_prompt_parts as _build_prompt_parts,
    disable_builtin_agent_types,
    excluded_tools as _excluded_tools,
    get_spec,
    register_agent_type,
    set_managed_builtin_agent_types,
    unregister_agent_type,
)
from ..kernel import Service, plugin

_logger = logging.getLogger(__name__)


def _import_attr(dotted: str):
    from .tool_plugin import import_attr

    return import_attr(dotted)


class SubAgentsService(Service):
    """SubAgent 运行时服务 — 占据 ``ctx.subagents``。"""

    provide = "subagents"
    name = "subagents"
    inject = ("agents", "prompt")

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        cfg = config or getattr(ctx, "config", None) or {}
        managed = cfg.get("managed_agent_types") or ()
        if managed:
            undo_managed = set_managed_builtin_agent_types(managed)
            ctx.effect(lambda: undo_managed)
        disabled = cfg.get("disabled_agent_types") or ()
        if disabled:
            undo_disabled = disable_builtin_agent_types(disabled)
            ctx.effect(lambda: undo_disabled)

    # ── 类型注册表 ───────────────────────────────────────

    def types(self) -> List[str]:
        return agent_type_names()

    def spec(self, name: str) -> AgentTypeSpec:
        return get_spec(name)

    def describe(self) -> List[dict]:
        return [spec.to_dict() for spec in active_specs().values()]

    def register_type(self, spec: AgentTypeSpec):
        """注册一个扩展 Agent 类型（注册即副作用，卸载时自动撤销）。"""
        undo = register_agent_type(spec)
        self.ctx.effect(lambda: undo)
        return undo

    def unregister_type(self, name: str) -> bool:
        return unregister_agent_type(name)

    def exclusions(self, agent_type: str) -> set:
        return _excluded_tools(agent_type)

    def build_prompt_parts(self, agent_type: str, cwd: Optional[str] = None) -> list:
        port = self.ctx.prompt.port
        return _build_prompt_parts(port, agent_type, cwd=cwd)

    # ── 执行器 / 工厂（可替换） ──────────────────────────

    def _executor_cls(self):
        ref = (self.config or {}).get("executor")
        if ref:
            return _import_attr(ref)
        from ..core.parallel_executor import ParallelExecutor

        return ParallelExecutor

    def _agent_factory(self):
        ref = (self.config or {}).get("agent_factory")
        if ref:
            return _import_attr(ref)
        from ..core.subagent import SubAgent

        return SubAgent

    def make_executor(self, parent_agent):
        """构造一个执行器实例（自省/测试用）。"""
        executor_cls = self._executor_cls()
        max_history = int((self.config or {}).get("max_history", 3) or 3)
        try:
            return executor_cls(parent_agent, max_history=max_history)
        except TypeError:
            return executor_cls(parent_agent)

    def spawn(self, parent_agent, spec: dict, index: int = 0, display: Any = None):
        """按 spec 构造一个 SubAgent（默认经 SubAgentSpawner 装配 display）。"""
        from ..core.internal.agent._subagent_spawner import SubAgentSpawner

        spawner = SubAgentSpawner(parent_agent, self._agent_factory())
        return spawner.spawn(spec, index, display)

    async def run(self, parent_agent, specs: Iterable[dict], max_workers: int = None):
        """并行执行一组子任务，返回结果字典列表。"""
        executor = self.make_executor(parent_agent)
        return await executor.run(list(specs), max_workers=max_workers)


@plugin("subagents", inject=["agents", "prompt"], provide=["subagents"])
def apply(ctx):
    return SubAgentsService(ctx, ctx.config)


__all__ = ["SubAgentsService", "apply"]
