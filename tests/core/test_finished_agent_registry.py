"""已完成 Agent 快照：``record_finished_agent`` / ``list_active_agents``。

用户需求 2026-11-04：``subagent_opt(op=list)`` 里**已完成的 subagent 也要
出现**。活跃注册表是弱引用（实例结束后随时被 GC），因此 ``SubAgent.run``
收尾时把展示字段写入有界历史 ``_finished_agents``；本文件覆盖：

- 结束后的快照仍被列出，状态为 completed / failed / cancelled；
- 实例被 GC 后快照仍在；
- 同一 task_id 重复登记覆盖旧快照（不产生重复条目）；
- 历史有界（超过上限淘汰最旧）；
- 排序：主 Agent 最前，其次运行中，最后已完成。
"""

from __future__ import annotations

import asyncio
import gc

import pytest

from src.core.base_agent import (
    _FINISHED_AGENTS_LIMIT,
    BaseAgent,
    _finished_agents,
    list_active_agents,
    record_finished_agent,
)


class _AgentStub(BaseAgent):
    """BaseAgent 最小实例：可直接指定 task_id / 类型 / 描述。"""

    def __init__(self, task_id: str, agent_type: str = "execute", description: str = ""):
        super().__init__()
        self.agent_id = task_id
        self.agent_type = agent_type
        self.description = description


@pytest.fixture(autouse=True)
def _isolate_agent_registries():
    """隔离全局完成历史，并在用例结束后把创建的实例标记为结束。"""
    created: list[BaseAgent] = []

    def make(task_id: str, agent_type: str = "execute", description: str = "") -> _AgentStub:
        agent = _AgentStub(task_id, agent_type, description)
        created.append(agent)
        return agent

    _finished_agents.clear()
    yield make
    for agent in created:
        agent._agent_finished = True
    _finished_agents.clear()


def _entries_by_id() -> dict[str, dict]:
    return {entry["task_id"]: entry for entry in list_active_agents()}


def test_finished_agent_remains_listed(_isolate_agent_registries) -> None:
    make = _isolate_agent_registries

    agent = make("sa-done", "execute", "只读盘点")
    agent._agent_finished = True
    record_finished_agent(agent, status="completed")

    entry = _entries_by_id()["sa-done"]
    assert entry == {
        "task_id": "sa-done",
        "agent_type": "execute",
        "description": "只读盘点",
        "status": "completed",
    }


def test_finished_snapshot_survives_instance_gc() -> None:
    agent = _AgentStub("sa-gc", "execute", "会被回收")
    agent._agent_finished = True
    record_finished_agent(agent, status="failed")

    del agent
    gc.collect()

    entry = _entries_by_id()["sa-gc"]
    assert entry["status"] == "failed"
    assert entry["description"] == "会被回收"


def test_same_task_id_replaces_snapshot(_isolate_agent_registries) -> None:
    make = _isolate_agent_registries

    first = make("sa-same", "execute", "旧描述")
    first._agent_finished = True
    record_finished_agent(first, status="failed")

    second = make("sa-same", "execute", "新描述")
    second._agent_finished = True
    record_finished_agent(second, status="completed")

    matches = [item for item in list_active_agents() if item["task_id"] == "sa-same"]
    assert matches == [{
        "task_id": "sa-same",
        "agent_type": "execute",
        "description": "新描述",
        "status": "completed",
    }]


def test_finished_history_is_bounded(_isolate_agent_registries) -> None:
    make = _isolate_agent_registries

    for index in range(_FINISHED_AGENTS_LIMIT + 5):
        agent = make(f"sa-{index:03d}")
        agent._agent_finished = True
        record_finished_agent(agent, status="completed")

    assert len(_finished_agents) == _FINISHED_AGENTS_LIMIT
    ids = {entry["task_id"] for entry in list_active_agents()}
    # 最旧的 5 条已被淘汰
    assert "sa-000" not in ids
    assert f"sa-{_FINISHED_AGENTS_LIMIT + 4:03d}" in ids


def test_main_then_running_then_finished_ordering(_isolate_agent_registries) -> None:
    make = _isolate_agent_registries

    make("main", "main")
    running = make("sa-running", "execute")
    finished = make("sa-done", "execute")
    finished._agent_finished = True
    record_finished_agent(finished, status="cancelled")

    entries = list_active_agents()
    sa_ids = [entry["task_id"] for entry in entries if entry["task_id"].startswith("sa-")]
    assert sa_ids.index("sa-running") < sa_ids.index("sa-done")
    assert entries[0]["agent_type"] == "main"
    assert running.agent_id == "sa-running"


# ── SubAgent.run 收尾登记（状态判定） ─────────────────────


def _make_subagent_stub(**attrs) -> object:
    """绕过 SubAgent.__init__ 的最小实例（只提供快照所需字段）。"""
    from src.core.subagent import SubAgent

    sub = object.__new__(SubAgent)
    sub.label = "sa-snapshot"
    sub.agent_id = "sa-snapshot"
    sub.agent_type = "execute"
    sub.description = "收尾登记"
    sub.error = ""
    for key, value in attrs.items():
        setattr(sub, key, value)
    return sub


def test_run_finally_snapshot_completed() -> None:
    sub = _make_subagent_stub()

    sub._record_finished_snapshot()

    assert _finished_agents[-1] == {
        "task_id": "sa-snapshot",
        "agent_type": "execute",
        "description": "收尾登记",
        "status": "completed",
    }


def test_run_finally_snapshot_failed() -> None:
    sub = _make_subagent_stub(error="模型调用失败")

    sub._record_finished_snapshot()

    assert _finished_agents[-1]["status"] == "failed"


def test_run_finally_snapshot_cancelled() -> None:
    sub = _make_subagent_stub()

    try:
        raise asyncio.CancelledError
    except asyncio.CancelledError:
        # 模拟 run() 的 finally：CancelledError 正在传播
        sub._record_finished_snapshot()

    assert _finished_agents[-1]["status"] == "cancelled"
