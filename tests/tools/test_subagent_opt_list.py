"""subagent_opt ``op=list`` — 列出当前所有 agent 的类型与 task_id。

覆盖：
- schema 暴露 taskless ``list`` op（无需 task_id），示例数量不超预算；
- ``display_params`` 对 list 不展示 task_id；
- list 返回主 Agent + 全部 SubAgent 的 ``task_id`` / ``agent_type``；
- 已结束实例（``_agent_finished``）不再作为「运行中」列出，但经
  ``record_finished_agent`` 登记的完成快照**仍会列出**，并带结束状态；
- 调用方主 Agent 的 subagent 后台任务表（含已完成记录）按 task_id 去重合并；
- 内核注册后 SubAgent 仍以 label（``sa-xxx``）列出且只一条；
- 未关联 Agent 上下文时仍能返回活跃注册表 + 完成历史快照。
"""

from __future__ import annotations

import json

import pytest

from src.core.base_agent import BaseAgent
from src.plugins.agents import AgentsService
from src.tools.subagent_opt import SubagentOptFunc


class _MainAgent(BaseAgent):
    """最小主 Agent 替身（BaseAgent 默认 agent_id="main" / agent_type="main"）。"""


class _SubAgentLike(BaseAgent):
    """最小 SubAgent 替身：只提供 list 展示所需的 label / 类型 / 描述。"""

    def __init__(self, task_id: str, agent_type: str, description: str = ""):
        super().__init__()
        self.label = task_id
        self.agent_id = task_id
        self.agent_type = agent_type
        self.description = description


class _FakeCtx:
    """``AgentsService`` 需要的最小上下文（provide / effect / notify / scope 派生）。"""

    def __init__(self) -> None:
        self.provided: dict[str, object] = {}
        self.effects: list = []

    def provide(self, name, value):
        self.provided[name] = value
        return lambda: self.provided.pop(name, None)

    def effect(self, callback):
        self.effects.append(callback)
        return lambda: None

    def notify(self, *args, **kwargs):
        pass

    def on(self, *args, **kwargs):
        return lambda: None

    def extend(self):
        return self

    def isolate_many(self, *names):
        return self


@pytest.fixture
def make_agent():
    """创建测试 Agent，并在用例结束后标记为结束（清理全局注册表视图）。"""
    created: list[BaseAgent] = []

    def _make(cls, *args, **kwargs):
        agent = cls(*args, **kwargs)
        created.append(agent)
        return agent

    yield _make

    for agent in created:
        agent._agent_finished = True


@pytest.fixture(autouse=True)
def _clean_finished_agent_history():
    """隔离全局已完成 Agent 历史（模块内每个用例前后清空）。"""
    from src.core.base_agent import _finished_agents

    _finished_agents.clear()
    yield
    _finished_agents.clear()


def test_schema_exposes_taskless_list_op() -> None:
    schema = SubagentOptFunc.to_tool_schema()
    function = schema["function"]
    props = function["parameters"]["properties"]

    assert "list" in props["op"]["enum"]
    assert "list" in function["description"]
    assert function["parameters"]["required"] == ["op"]
    # list 与 wait_all 同属「无需任务」的 op，模型可见说明必须一致
    assert "wait_all / list 可省略" in props["task_id"]["description"]
    # description 示例数保持在 1-2 条（与 test_tool_schema_prompts 同口径）
    assert 1 <= function["description"].count('({"') <= 2


def test_display_params_hides_task_id_for_list() -> None:
    assert SubagentOptFunc.display_params({"op": "list"}) == "'list'"
    assert SubagentOptFunc.display_params(
        {"op": "list", "task_id": "sa-ignored"},
    ) == "'list'"


async def test_list_includes_main_and_all_agent_types(make_agent) -> None:
    main = make_agent(_MainAgent)
    make_agent(_SubAgentLike, "sa-exec", "execute", "分析: src/tools")
    make_agent(_SubAgentLike, "sa-map", "map")

    func = SubagentOptFunc(op="list")
    func.set_agent(main)
    payload = json.loads(await func.execute())

    assert payload["count"] == len(payload["agents"])
    by_id = {item["task_id"]: item for item in payload["agents"]}
    assert by_id["main"]["agent_type"] == "main"
    assert by_id["sa-exec"] == {
        "task_id": "sa-exec",
        "agent_type": "execute",
        "description": "分析: src/tools",
        "status": "running",
    }
    assert by_id["sa-map"]["agent_type"] == "map"
    # 主 Agent 排在最前
    assert payload["agents"][0]["agent_type"] == "main"


async def test_finished_agent_without_snapshot_not_listed(make_agent) -> None:
    """仅置 ``_agent_finished``（未登记完成快照）的实例不作为运行中列出。"""
    main = make_agent(_MainAgent)
    finished = make_agent(_SubAgentLike, "sa-finished", "review")
    finished._agent_finished = True

    func = SubagentOptFunc(op="list")
    func.set_agent(main)
    payload = json.loads(await func.execute())

    assert "sa-finished" not in {item["task_id"] for item in payload["agents"]}


async def test_list_merges_pending_background_tasks(make_agent) -> None:
    main = make_agent(_MainAgent)
    make_agent(_SubAgentLike, "sa-dup", "execute")
    main._subagent_tasks = {
        # 与活跃实例同 id：必须去重，只出现一次
        "sa-dup": {
            "agent_type": "execute",
            "description": "重复项",
            "status": "running",
            "done": False,
        },
        # 已返回 task_id 但 SubAgent 实例尚未创建：仍需列出
        "sa-pending": {
            "agent_type": "plan",
            "description": "写计划",
            "status": "running",
            "done": False,
        },
        # 已完成记录同样列出（status=completed，用户需求 2026-11-04）
        "sa-done": {
            "agent_type": "execute",
            "description": "已完成",
            "status": "completed",
            "done": True,
        },
    }

    func = SubagentOptFunc(op="list")
    func.set_agent(main)
    payload = json.loads(await func.execute())
    ids = [item["task_id"] for item in payload["agents"]]

    assert ids.count("sa-dup") == 1
    assert "sa-pending" in ids
    assert "sa-done" in ids
    done = next(item for item in payload["agents"] if item["task_id"] == "sa-done")
    assert done["status"] == "completed"
    pending = next(item for item in payload["agents"] if item["task_id"] == "sa-pending")
    assert pending["agent_type"] == "plan"
    assert pending["description"] == "写计划"
    # 运行中条目排在已完成条目之前（主 Agent 恒在最前）
    assert ids.index("sa-pending") < ids.index("sa-done")


async def test_list_without_agent_context_returns_registry(make_agent) -> None:
    make_agent(_MainAgent)

    payload = json.loads(await SubagentOptFunc(op="list").execute())

    assert payload["count"] >= 1
    assert any(item["agent_type"] == "main" for item in payload["agents"])


async def test_kernel_registered_subagent_listed_once_with_task_id(make_agent) -> None:
    """内核注册后 SubAgent 仍以 label（``sa-xxx``）列出，且只列一条。

    回归：``AgentsService.register`` 曾用记录 id 覆盖 ``agent_id``——同一
    子代理会同时以 ``execute-N``（活跃注册表）与 ``sa-xxx``（后台任务表）
    出现两条，且 ``execute-N`` 拿去 read/wait/kill 会被 task_id 前缀校验拒绝。
    """
    main = make_agent(_MainAgent)
    sub = make_agent(_SubAgentLike, "sa-live", "execute", "只读盘点")
    record = AgentsService(_FakeCtx()).register(sub, kind="execute")
    assert record.id.startswith("execute-")
    main._subagent_tasks = {
        "sa-live": {
            "agent_type": "execute",
            "description": "只读盘点",
            "status": "running",
            "done": False,
        },
    }

    func = SubagentOptFunc(op="list")
    func.set_agent(main)
    payload = json.loads(await func.execute())

    ids = [item["task_id"] for item in payload["agents"]]
    assert ids == ["main", "sa-live"]
    assert payload["count"] == len(payload["agents"]) == 2
    assert all(not item["task_id"].startswith("execute-") for item in payload["agents"])
    live = payload["agents"][1]
    assert live == {
        "task_id": "sa-live",
        "agent_type": "execute",
        "description": "只读盘点",
        "status": "running",
    }


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
async def test_finished_agent_listed_with_status(make_agent, status: str) -> None:
    """已完成的 SubAgent 仍列出，并带回结束状态（completed/failed/cancelled）。

    用户需求 2026-11-04：完成的 subagent 不能从 list 消失——``SubAgent.run``
    收尾时经 ``record_finished_agent`` 登记有界快照，实例结束后仍可见。
    """
    from src.core.base_agent import record_finished_agent

    main = make_agent(_MainAgent)
    finished = make_agent(_SubAgentLike, "sa-done", "execute", "已结束任务")
    finished._agent_finished = True
    record_finished_agent(finished, status=status)

    func = SubagentOptFunc(op="list")
    func.set_agent(main)
    payload = json.loads(await func.execute())

    by_id = {item["task_id"]: item for item in payload["agents"]}
    assert by_id["sa-done"] == {
        "task_id": "sa-done",
        "agent_type": "execute",
        "description": "已结束任务",
        "status": status,
    }
    # 运行中的主 Agent 排在已完成条目之前
    assert payload["agents"][0]["task_id"] == "main"


async def test_finished_snapshot_and_task_record_merged_once(make_agent) -> None:
    """完成快照与后台任务表的同 id 记录合并为一条（不重复列出）。"""
    from src.core.base_agent import record_finished_agent

    main = make_agent(_MainAgent)
    finished = make_agent(_SubAgentLike, "sa-consumed", "execute", "已消费")
    finished._agent_finished = True
    record_finished_agent(finished, status="completed")
    main._subagent_tasks = {
        "sa-consumed": {
            "agent_type": "execute",
            "description": "已消费",
            "status": "completed",
            "done": True,
        },
    }

    func = SubagentOptFunc(op="list")
    func.set_agent(main)
    payload = json.loads(await func.execute())

    ids = [item["task_id"] for item in payload["agents"]]
    assert ids.count("sa-consumed") == 1
    assert payload["count"] == len(payload["agents"]) == 2
