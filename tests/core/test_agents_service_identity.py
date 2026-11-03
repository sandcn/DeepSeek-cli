"""AgentsService 反向绑定：内核记录 id 不得覆盖 Agent 自有身份。

回归场景（``subagent_opt(op=list)`` 给出不可用 id / 同一子代理列两次）：
``AgentsService.register`` 曾无条件 ``setattr(agent, "agent_id", record.id)``，
把 SubAgent 的 ``sa-xxx`` 覆盖成 ``execute-N``。修复后的契约：

- ``agent_id`` 保持 Agent **自有身份**（主 Agent ``main`` / SubAgent ``sa-xxx``
  ——即沙盒锁、读审计与 subagent 任务句柄使用的标识）；
- 内核记录 id 经 ``agent_kernel_id`` 暴露，仍可按 id 查注册表；
- Agent 自身没有身份时才用记录 id 回填（测试桩 / 第三方 Agent 兼容）。
"""

from __future__ import annotations

import pytest

from src.core.base_agent import BaseAgent
from src.plugins.agents import AgentsService


class _FakeCtx:
    """AgentsService 需要的最小上下文（provide / effect / notify / scope 派生）。"""

    def __init__(self) -> None:
        self.provided: dict[str, object] = {}
        self.effects: list = []
        self.notified: list = []

    def provide(self, name, value):
        self.provided[name] = value
        return lambda: self.provided.pop(name, None)

    def effect(self, callback):
        self.effects.append(callback)
        return lambda: None

    def notify(self, *args, **kwargs):
        self.notified.append((args, kwargs))

    def on(self, *args, **kwargs):
        return lambda: None

    def extend(self):
        return self

    def isolate_many(self, *names):
        return self


class _AgentStub(BaseAgent):
    """BaseAgent 最小实例（默认 ``agent_id="main"``）。"""


@pytest.fixture
def service() -> AgentsService:
    return AgentsService(_FakeCtx())


@pytest.fixture
def agent() -> _AgentStub:
    stub = _AgentStub()
    yield stub
    # 全局活跃注册表是 WeakSet：标记结束避免影响其它用例的 list 视图
    stub._agent_finished = True


def test_register_keeps_subagent_identity(service: AgentsService, agent: _AgentStub) -> None:
    agent.agent_id = "sa-abc123"

    record = service.register(agent, kind="execute")

    assert record.id == "execute-1"
    # 自有身份不被注册表编号覆盖（修复点）
    assert agent.agent_id == "sa-abc123"
    # 记录 id 仍可从 Agent 读到，且注册表可按它反查
    assert agent.agent_kernel_id == "execute-1"
    assert agent.agent_kind == "execute"
    assert agent.agent_record is record
    assert service.get("execute-1") is record
    assert service.of(agent) is record


def test_register_keeps_main_identity(service: AgentsService, agent: _AgentStub) -> None:
    record = service.register(agent, kind="main")

    assert record.id == "main-1"
    assert agent.agent_id == "main"


def test_register_backfills_agent_id_when_absent(
    service: AgentsService, agent: _AgentStub,
) -> None:
    agent.agent_id = ""

    record = service.register(agent, kind="main")

    assert agent.agent_id == record.id == "main-1"
    assert agent.agent_kernel_id == record.id


def test_register_is_idempotent_for_same_agent(service: AgentsService, agent: _AgentStub) -> None:
    first = service.register(agent, kind="execute")
    second = service.register(agent, kind="plan")

    assert first is second
    assert agent.agent_id == "main"
    assert agent.agent_kernel_id == "execute-1"


def test_unregister_keeps_identity_and_clears_registry(
    service: AgentsService, agent: _AgentStub,
) -> None:
    agent.agent_id = "sa-abc123"
    service.register(agent, kind="execute")

    assert service.unregister(agent) is True
    assert service.of(agent) is None
    assert agent.agent_id == "sa-abc123"
