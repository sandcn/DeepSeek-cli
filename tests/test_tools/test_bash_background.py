"""bash 工具后台执行模式（background=True）测试。

覆盖需求：
1. bash.py 增加 background 参数；后台模式下生成 task_id 给大模型
   （返回 {"task_id": ..., "status": "running", "command": ...} JSON）
2. tasklist 放到对应 agent 的成员（_background_tasks）中
3. 后台命令完成后，在模型「思考回答 + 工具调用一轮完成」后插入用户消息
   （JSON 格式：task_id + 命令输出）
4. 大模型完成所有对话但后台任务未完成时，等待后台任务完成，
   把结果 JSON 插入用户消息再来一轮对话
"""

from __future__ import annotations

import asyncio
import json

import pytest

from src.core.agent import Agent
from src.core.ports.model import ModelResult
from src.core.subagent import SubAgent
from src.tools.bash import BashFunc


# ═══════════════════════════════════════════════════════════
# 辅助：Fake 模型端口（可编程调用序列）
# ═══════════════════════════════════════════════════════════

class _FakeModelPort:
    """可编程模型端口：按调用序号返回预设结果。

    - 第 1 次调用：返回 tool_calls=[bash background]
    - 后续调用：返回普通 content
    """

    def __init__(self, bg_command: str = "sleep 0.3 && echo hello-bg",
                 bg_timeout: float = 15.0):
        self.bg_command = bg_command
        self.bg_timeout = bg_timeout
        self.call_count = 0
        self.results: list[ModelResult] = []

    def enqueue(self, result: ModelResult) -> None:
        self.results.append(result)

    async def call(self, messages, model=None, tools=None, display=None,
                   label=None, silent=False):
        self.call_count += 1
        if self.results:
            return self.results.pop(0)
        if self.call_count == 1:
            return ModelResult(
                content="",
                usage={"input": 1, "output": 1},
                tool_calls=[{
                    "id": "call_1",
                    "name": "bash",
                    "arguments": {"command": self.bg_command, "background": True},
                }],
            )
        return ModelResult(
            content=f"回复-第{self.call_count}轮",
            usage={"input": 1, "output": 1},
            tool_calls=[],
        )

    async def call_sync(self, messages, model=None, tools=None, display=None,
                        label=None):
        return await self.call(messages, model, tools, display, label)


# ═══════════════════════════════════════════════════════════
# BashFunc 后台执行基础行为
# ═══════════════════════════════════════════════════════════

class TestBashBackgroundBasics:
    """bash 工具 background 参数的基础行为。"""

    def test_schema_contains_background_param(self) -> None:
        """schema 中包含 background 参数。"""
        schema = BashFunc.to_tool_schema()
        props = schema["function"]["parameters"]["properties"]
        assert "background" in props
        assert props["background"]["type"] == "boolean"

    def test_constructor_accepts_background(self) -> None:
        """__init__ 接受 background 参数并转为布尔。"""
        assert BashFunc(command="echo hi", background=True).background is True
        assert BashFunc(command="echo hi", background=0).background is False
        assert BashFunc(command="echo hi").background is False

    @pytest.mark.asyncio
    async def test_background_requires_agent(self) -> None:
        """未关联 Agent 时后台执行返回错误提示。"""
        result = await BashFunc(command="echo hi", background=True).execute()
        assert "后台执行需要关联 Agent" in result

    @pytest.mark.asyncio
    async def test_background_returns_task_id_json_and_registers(self) -> None:
        """后台模式返回 task_id JSON，并把任务注册到 agent 的 _background_tasks。"""
        agent = Agent(model="fake-model")
        func = agent.get_tool_registry().dispatch(
            "bash",
            {"command": "sleep 0.2 && echo bg-done", "background": True},
            agent=agent,
        )
        ret = await func.execute()

        data = json.loads(ret)
        assert data["status"] == "running"
        assert data["task_id"].startswith("bg-")
        assert data["command"] == "sleep 0.2 && echo bg-done"
        # tasklist 放入对应 agent 的成员
        assert data["task_id"] in agent._background_tasks

        # 等待后台任务完成，结果写入 record
        rec = agent._background_tasks[data["task_id"]]
        await asyncio.wait_for(rec["task"], timeout=15)
        assert rec["done"] is True
        assert rec["result"] == "bg-done"

    @pytest.mark.asyncio
    async def test_background_runs_without_blocking(self) -> None:
        """后台模式不阻塞：立即返回（远快于命令本身执行时间）。"""
        agent = Agent(model="fake-model")
        func = agent.get_tool_registry().dispatch(
            "bash",
            {"command": "sleep 1 && echo slow", "background": True},
            agent=agent,
        )
        started = asyncio.get_event_loop().time()
        ret = await func.execute()
        elapsed = asyncio.get_event_loop().time() - started
        # 命令 sleep 1s，后台模式应在远小于 1s 内返回
        assert elapsed < 0.5
        data = json.loads(ret)
        assert data["status"] == "running"

        # 等待后台任务真正完成
        rec = agent._background_tasks[data["task_id"]]
        await asyncio.wait_for(rec["task"], timeout=15)
        assert rec["result"] == "slow"

    @pytest.mark.asyncio
    async def test_background_dangerous_command_rejected(self) -> None:
        """后台模式同样拒绝危险命令（display 路径运行时防护）。"""
        agent = Agent(model="fake-model")
        func = agent.get_tool_registry().dispatch(
            "bash",
            {"command": "sudo ls /", "background": True},
            agent=agent,
        )
        ret = await func.execute()
        assert "拒绝执行危险命令" in ret
        assert agent._background_tasks == {}


# ═══════════════════════════════════════════════════════════
# BaseAgent 后台任务处理（_process_background_tasks）
# ═══════════════════════════════════════════════════════════

class TestProcessBackgroundTasks:
    """一轮对话完成后处理后台任务。"""

    @pytest.mark.asyncio
    async def test_no_background_tasks_returns_false(self) -> None:
        """无后台任务时 _process_background_tasks 返回 False（对话可结束）。"""
        agent = Agent(model="fake-model")
        assert await agent._process_background_tasks() is False

    @pytest.mark.asyncio
    async def test_done_task_inserts_json_user_message(self) -> None:
        """已完成的后台任务 → 插入 JSON 用户消息（taskid + 命令输出）并从 tasklist 移除。"""
        agent = Agent(model="fake-model")
        func = agent.get_tool_registry().dispatch(
            "bash",
            {"command": "echo 42", "background": True},
            agent=agent,
        )
        ret = await func.execute()
        task_id = json.loads(ret)["task_id"]

        # 等待后台任务完成
        rec = agent._background_tasks[task_id]
        await asyncio.wait_for(rec["task"], timeout=15)

        # 一轮对话完成后处理
        proceed = await agent._process_background_tasks()
        assert proceed is True

        # 最后一条消息是 user 角色、JSON 格式、含 task_id 和命令输出
        last = agent.messages[-1]
        assert last["role"] == "user"
        msg = json.loads(last["content"])
        assert msg["task_id"] == task_id
        assert msg["output"] == "42"
        assert msg["command"] == "echo 42"
        assert msg["status"] == "completed"
        # tasklist 已移除该任务
        assert task_id not in agent._background_tasks

    @pytest.mark.asyncio
    async def test_running_task_is_waited_then_inserted(self) -> None:
        """无已完成但有运行中的后台任务 → 等待全部完成后插入结果消息。"""
        agent = Agent(model="fake-model")
        func = agent.get_tool_registry().dispatch(
            "bash",
            {"command": "sleep 0.5 && echo waited", "background": True},
            agent=agent,
        )
        ret = await func.execute()
        task_id = json.loads(ret)["task_id"]

        # 立即调用（此时后台任务必然仍在运行）
        proceed = await agent._process_background_tasks()
        assert proceed is True  # 等待完成后插入

        last = agent.messages[-1]
        assert last["role"] == "user"
        msg = json.loads(last["content"])
        assert msg["task_id"] == task_id
        assert msg["output"] == "waited"

    @pytest.mark.asyncio
    async def test_multiple_tasks_all_inserted(self) -> None:
        """多个后台任务完成 → 全部插入对应 JSON 用户消息。"""
        agent = Agent(model="fake-model")
        task_ids = []
        for i in range(3):
            ret = await agent.get_tool_registry().dispatch(
                "bash",
                {"command": f"echo out-{i}", "background": True},
                agent=agent,
            ).execute()
            task_ids.append(json.loads(ret)["task_id"])

        # 等待全部完成
        for task_id in task_ids:
            rec = agent._background_tasks[task_id]
            await asyncio.wait_for(rec["task"], timeout=15)

        proceed = await agent._process_background_tasks()
        assert proceed is True

        # 三条消息全部插入（JSON 中按 task_id 匹配）
        user_msgs = [m for m in agent.messages if m["role"] == "user"]
        assert len(user_msgs) == 3
        parsed_outputs = {json.loads(m["content"])["output"] for m in user_msgs}
        assert parsed_outputs == {"out-0", "out-1", "out-2"}
        assert agent._background_tasks == {}


# ═══════════════════════════════════════════════════════════
# Agent.run 完整循环（主 Agent）
# ═══════════════════════════════════════════════════════════

class TestAgentRunBackgroundLoop:
    """Agent.run() 中「一轮完成后插入后台结果 → 再来一轮对话」的完整编排。"""

    @pytest.mark.asyncio
    async def test_background_completes_and_continues_round(self) -> None:
        """后台任务完成后插入用户消息，模型继续一轮对话处理结果。"""
        port = _FakeModelPort()
        agent = Agent(model="fake-model", async_model_port=port)

        interrupted = await agent.run()
        assert interrupted is False

        # 模型调用 3 次：①调用后台 bash ②后台未完成时的中间回复 ③处理后台结果
        assert port.call_count == 3

        # 消息流：assistant(tool_calls=bash) → tool(JSON) → assistant(中间回复)
        #       → user(后台结果 JSON) → assistant(最终回复)
        roles = [m["role"] for m in agent.messages if m["role"] != "system"]
        assert roles == ["assistant", "tool", "assistant", "user", "assistant"]

        # 后台结果消息：JSON 含 task_id 和命令输出
        bg_msg = next(m for m in agent.messages
                      if m["role"] == "user" and m["content"].startswith("{"))
        data = json.loads(bg_msg["content"])
        assert data["task_id"].startswith("bg-")
        assert data["output"] == "hello-bg"
        assert agent._background_tasks == {}

    @pytest.mark.asyncio
    async def test_no_background_single_round(self) -> None:
        """无后台任务时 Agent.run 只跑一轮（行为不变）。"""
        class _SimplePort(_FakeModelPort):
            async def call(self, messages, model=None, tools=None, display=None,
                           label=None, silent=False):
                self.call_count += 1
                return ModelResult(
                    content="普通回复", usage={"input": 1, "output": 1}, tool_calls=[],
                )

        port = _SimplePort()
        agent = Agent(model="fake-model", async_model_port=port)
        interrupted = await agent.run()
        assert interrupted is False
        assert port.call_count == 1
        assert agent._background_tasks == {}

    @pytest.mark.asyncio
    async def test_background_completes_before_round_end(self) -> None:
        """两个后台任务：快任务在对话期间完成，慢任务在对话结束仍未完成。

        验证：
        - 快任务完成后结果在当轮结束插入，并继续一轮对话
        - 慢任务未完成时等待全部完成后插入结果，再来一轮对话
        """
        port = _FakeModelPort()
        # 第1次：快后台任务；第2次：慢后台任务；第3次：普通回复（对话结束）
        port.enqueue(ModelResult(
            content="", usage={"input": 1, "output": 1},
            tool_calls=[{
                "id": "call_2", "name": "bash",
                "arguments": {"command": "echo quick", "background": True},
            }],
        ))
        port.enqueue(ModelResult(
            content="", usage={"input": 1, "output": 1},
            tool_calls=[{
                "id": "call_3", "name": "bash",
                "arguments": {"command": "sleep 0.5 && echo slow", "background": True},
            }],
        ))
        port.enqueue(ModelResult(
            content="本轮对话结束", usage={"input": 1, "output": 1}, tool_calls=[],
        ))

        agent = Agent(model="fake-model", async_model_port=port)
        interrupted = await agent.run()
        assert interrupted is False

        # 两个后台任务结果都以 JSON 用户消息插入
        bg_msgs = [
            json.loads(m["content"]) for m in agent.messages
            if m["role"] == "user" and m["content"].startswith("{")
        ]
        assert len(bg_msgs) == 2
        assert {m["output"] for m in bg_msgs} == {"quick", "slow"}
        assert agent._background_tasks == {}


# ═══════════════════════════════════════════════════════════
# SubAgent 后台循环
# ═══════════════════════════════════════════════════════════

class _FakeParentPort:
    async def call(self, messages, model=None, tools=None, display=None,
                   label=None, silent=False):
        return ModelResult(content="父结果", usage={"input": 1, "output": 1}, tool_calls=[])

    async def call_sync(self, messages, model=None, tools=None, display=None,
                        label=None):
        return ModelResult(content="父结果", usage={"input": 1, "output": 1}, tool_calls=[])


class _FakeSubPort(_FakeModelPort):
    """SubAgent 用模型端口（silent 调用路径）。"""

    async def call(self, messages, model=None, tools=None, display=None,
                   label=None, silent=False, **kwargs):
        return await super().call(messages, model, tools, display, label, silent)


class TestSubAgentBackgroundLoop:
    """SubAgent 在独立循环中处理后台任务。"""

    @pytest.mark.asyncio
    async def test_subagent_background_waits_and_continues(self) -> None:
        """SubAgent 完成后台任务后把结果插入用户消息并继续一轮。"""
        parent = Agent(model="fake-model", async_model_port=_FakeParentPort())
        port = _FakeSubPort()
        sub = SubAgent(
            label="agent-1", description="测试", prompt="请执行任务",
            parent_agent=parent, model="fake-model", agent_type="execute",
        )
        sub._model_port = port

        result = await asyncio.wait_for(sub.run(), timeout=15)
        assert "回复" in result
        # 3 次模型调用：①后台 bash ②中间回复(等待后台) ③处理后台结果
        assert port.call_count == 3

        # 后台结果 JSON 用户消息已插入 SubAgent 消息列表
        bg_msgs = [
            json.loads(m["content"]) for m in sub.messages
            if m["role"] == "user" and m["content"].startswith("{")
        ]
        assert len(bg_msgs) == 1
        assert bg_msgs[0]["output"] == "hello-bg"
        assert sub._background_tasks == {}
