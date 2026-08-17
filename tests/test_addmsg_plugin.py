"""/addmsg 命令与 AddmsgMiddleware 流式插入测试。

覆盖链路（2026-08-17 用户需求）：
1. BaseAgent.addmsg 队列（add_addmsg / drain_addmsg / has_pending_addmsg / insert_addmsg_messages）
2. InputBufferEditor.peek_queued_input（查看不消费）
3. AddmsgMiddleware 阶段完成点插入（after_model_call / after_tool_execution）
4. AddmsgMiddleware 捕获流式期间用户输入的 /addmsg 命令
5. Pipeline：addmsg 插入后无工具调用也继续下一轮模型调用
6. AddmsgPlugin 命令分发（无参数 / 无流式 run_round / 有流式排队 / SubAgent 拒绝）
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from src.core.adapters.interrupt import MockInterruptAdapter
from src.core.adapters.null import _NullDisplayPort, _NullEventPort, _NullOutputPort
from src.core.agent import Agent
from src.core.base_agent import BaseAgent
from src.core.middleware.addmsg import AddmsgMiddleware
from src.core.middleware.interrupt import _InterruptCheckMiddleware
from src.core.pipeline import PipelineContext


def _make_agent() -> Agent:
    """构造无 UI 依赖的测试 Agent（pipeline 含 AddmsgMiddleware）。"""
    agent = Agent(
        model="test-model",
        display_port=_NullDisplayPort(),
        event_port=_NullEventPort(),
        output_port=_NullOutputPort(),
    )
    agent._interrupt_port = MockInterruptAdapter()
    # 替换 pipeline 中的中断检查中间件（默认端口依赖全局中断标志）
    for i, mw in enumerate(agent.pipeline._async_middlewares):
        if mw.name == "InterruptCheck":
            agent.pipeline._async_middlewares[i] = _InterruptCheckMiddleware(
                MockInterruptAdapter()
            )
    return agent


# ── BaseAgent.addmsg 队列 ────────────────────────────────

class TestBaseAgentAddmsgQueue:
    def test_add_and_drain(self):
        a = BaseAgent()
        assert a.has_pending_addmsg() is False
        a.add_addmsg("消息1")
        a.add_addmsg("消息2")
        assert a.has_pending_addmsg() is True
        assert a.drain_addmsg() == ["消息1", "消息2"]
        assert a.has_pending_addmsg() is False
        assert a.drain_addmsg() == []

    def test_add_empty_ignored(self):
        a = BaseAgent()
        a.add_addmsg("")
        a.add_addmsg(None)
        a.add_addmsg(123)  # 非字符串转字符串
        assert a.drain_addmsg() == ["123"]
    def test_insert_addmsg_messages(self):
        a = BaseAgent()
        a.messages.append({"role": "system", "content": "sys"})
        a.insert_addmsg_messages(["补充1", "补充2"])
        roles = [m["role"] for m in a.messages]
        assert roles == ["system", "user", "user"]
        assert a.messages[1]["content"] == "补充1"
        assert a.messages[2]["content"] == "补充2"

    def test_insert_empty_noop(self):
        a = BaseAgent()
        a.messages.append({"role": "system", "content": "sys"})
        a.insert_addmsg_messages([])
        assert len(a.messages) == 1


# ── InputBufferEditor.peek_queued_input ──────────────────

class TestPeekQueuedInput:
    def test_peek_no_queue(self):
        from src.tui._input_buffer import InputBufferEditor
        be = InputBufferEditor(history_file=None)
        assert be.peek_queued_input() is None

    def test_peek_does_not_consume(self):
        from src.tui._input_buffer import InputBufferEditor
        be = InputBufferEditor(history_file=None)
        be.set_buffer("hello")
        be._enter(append_history=lambda t: None)
        assert be.peek_queued_input() == "hello"
        assert be.peek_queued_input() == "hello"  # 多次 peek 不消费
        assert be.get_queued_input() == "hello"  # 消费
        assert be.peek_queued_input() is None


# ── AddmsgMiddleware ─────────────────────────────────────

class _FakeInput:
    """模拟 Input 的 peek/get 行为。"""

    def __init__(self, queued):
        self._queued = queued

    def peek_queued_input(self):
        return self._queued

    def get_queued_input(self):
        q = self._queued
        self._queued = None
        return q


class TestAddmsgMiddleware:
    def _make_ctx(self, agent, addmsg_queue=None):
        if addmsg_queue is not None:
            agent._addmsg_queue = list(addmsg_queue)
        ctx = PipelineContext(agent)
        ctx.addmsg_inserted = False
        return ctx

    async def test_after_model_call_inserts(self):
        agent = _make_agent()
        ctx = self._make_ctx(agent, ["补充消息"])
        mw = AddmsgMiddleware()
        await mw.after_model_call(ctx)
        assert ctx.addmsg_inserted is True
        assert any(
            m.get("role") == "user" and m.get("content") == "补充消息"
            for m in agent.messages
        )

    async def test_after_model_call_no_pending_resets_flag(self):
        agent = _make_agent()
        ctx = self._make_ctx(agent)
        ctx.addmsg_inserted = True  # 上一轮残留标志
        mw = AddmsgMiddleware()
        await mw.after_model_call(ctx)
        assert ctx.addmsg_inserted is False  # 已重置

    async def test_after_tool_execution_inserts(self):
        agent = _make_agent()
        ctx = self._make_ctx(agent, ["工具后补充"])
        mw = AddmsgMiddleware()
        await mw.after_tool_execution(ctx)
        assert ctx.addmsg_inserted is True
        assert any(
            m.get("role") == "user" and m.get("content") == "工具后补充"
            for m in agent.messages
        )

    async def test_capture_addmsg_from_input(self):
        agent = _make_agent()
        fake = _FakeInput("/addmsg 补充内容")
        agent.set_addmsg_input_provider(lambda: fake)
        ctx = self._make_ctx(agent)
        mw = AddmsgMiddleware()
        await mw.after_model_call(ctx)
        assert fake._queued is None  # /addmsg 输入已消费
        assert ctx.addmsg_inserted is True
        assert any(
            m.get("role") == "user" and m.get("content") == "补充内容"
            for m in agent.messages
        )

    async def test_capture_ignores_plain_input(self):
        agent = _make_agent()
        fake = _FakeInput("普通消息")
        agent.set_addmsg_input_provider(lambda: fake)
        ctx = self._make_ctx(agent)
        mw = AddmsgMiddleware()
        await mw.after_model_call(ctx)
        assert fake._queued == "普通消息"  # 未消费，保留给 round_end
        assert ctx.addmsg_inserted is False

    async def test_capture_no_provider_safe(self):
        agent = _make_agent()
        ctx = self._make_ctx(agent, ["排队消息"])
        mw = AddmsgMiddleware()
        await mw.after_model_call(ctx)  # 无 provider 不崩溃
        assert ctx.addmsg_inserted is True
        assert any(
            m.get("role") == "user" and m.get("content") == "排队消息"
            for m in agent.messages
        )

    async def test_capture_addmsg_without_content_ignored(self):
        agent = _make_agent()
        fake = _FakeInput("/addmsg")
        agent.set_addmsg_input_provider(lambda: fake)
        ctx = self._make_ctx(agent)
        mw = AddmsgMiddleware()
        await mw.after_model_call(ctx)
        assert fake._queued is None  # 命令被消费
        assert ctx.addmsg_inserted is False  # 无内容不插入
        assert not any(
            m.get("role") == "user" and m.get("content") == ""
            for m in agent.messages
        )


# ── Pipeline 集成：addmsg 后继续循环 ─────────────────────

class TestPipelineAddmsgContinue:
    async def test_pipeline_continues_after_addmsg(self):
        agent = _make_agent()
        agent.messages.append({"role": "user", "content": "原始问题"})
        agent._addmsg_queue = ["中途补充"]
        calls = []

        async def fake_call(messages, model=None, tools=None, display=None,
                            label=None, silent=False):
            calls.append(list(messages))
            return "", f"第{len(calls)}次回答", {"input": 0, "output": 0, "calls": 1}, []

        agent._call_model_async = fake_call

        ctx = PipelineContext(agent)
        ctx.interrupt_port = MockInterruptAdapter()
        interrupted = await agent.pipeline.run_round_async(ctx)

        assert interrupted is False
        assert len(calls) == 2  # 第一次回答 + addmsg 后的继续
        # 第二次模型调用携带插入的 user 消息
        assert any(
            m.get("role") == "user" and m.get("content") == "中途补充"
            for m in calls[1]
        )
        # 消息顺序：system + user(原始) + assistant(回答) + user(补充) + assistant(继续回答)
        roles = [m["role"] for m in agent.messages]
        assert roles == ["system", "system", "user", "assistant", "user", "assistant"]
        # 插入的 user 消息位于两次 assistant 回答之间
        user_contents = [m["content"] for m in agent.messages if m["role"] == "user"]
        assert user_contents == ["原始问题", "中途补充"]

    async def test_pipeline_round_completes_without_addmsg(self):
        agent = _make_agent()
        agent.messages.append({"role": "user", "content": "原始问题"})
        calls = []

        async def fake_call(messages, model=None, tools=None, display=None,
                            label=None, silent=False):
            calls.append(list(messages))
            return "", "回答", {"input": 0, "output": 0, "calls": 1}, []

        agent._call_model_async = fake_call

        ctx = PipelineContext(agent)
        ctx.interrupt_port = MockInterruptAdapter()
        interrupted = await agent.pipeline.run_round_async(ctx)

        assert interrupted is False
        assert len(calls) == 1  # 无 addmsg → 一轮结束
        assert ctx.round_complete is True

    async def test_pipeline_inserts_after_tool_execution(self):
        agent = _make_agent()
        agent.messages.append({"role": "user", "content": "原始问题"})
        agent._addmsg_queue = ["工具后补充"]
        calls = []
        tool_handled = []

        async def fake_call(messages, model=None, tools=None, display=None,
                            label=None, silent=False):
            calls.append(list(messages))
            if len(calls) == 1:
                # 第一次调用返回工具调用
                return "", "", {"input": 0, "output": 0, "calls": 1}, [
                    {"id": "call_1", "name": "read_file", "arguments": "{}"}
                ]
            return "", "最终回答", {"input": 0, "output": 0, "calls": 1}, []

        async def fake_handle_tool_calls(content, tool_calls, reasoning=None, usage=None):
            tool_handled.append(tool_calls)
            agent._append_tool_result("call_1", "工具结果")

        agent._call_model_async = fake_call
        agent._handle_tool_calls = fake_handle_tool_calls

        ctx = PipelineContext(agent)
        ctx.interrupt_port = MockInterruptAdapter()
        interrupted = await agent.pipeline.run_round_async(ctx)

        assert interrupted is False
        assert len(calls) == 2
        assert len(tool_handled) == 1
        # 第二次模型调用携带工具结果 + 插入的 user 消息
        assert any(
            m.get("role") == "tool" and m.get("tool_call_id") == "call_1"
            for m in calls[1]
        )
        assert any(
            m.get("role") == "user" and m.get("content") == "工具后补充"
            for m in calls[1]
        )


# ── AddmsgPlugin 命令分发 ────────────────────────────────

class TestAddmsgPlugin:
    def _make_ctx(self, session, arg):
        return MagicMock(session=session, arg=arg, state={"model": "test-model"})

    def _make_session(self, agent, state="idle"):
        session = MagicMock()
        session.agent = agent
        session.model = "test-model"
        session.state_machine = MagicMock()
        session.state_machine.is_.return_value = (state == "running")
        return session

    def test_registered(self):
        from src.core.commands.plugins import get_interactive_registry
        reg = get_interactive_registry()
        assert reg.exists("addmsg") is True

    async def test_no_argument_returns_true(self):
        from src.core.commands.plugins.addmsg_plugin import AddmsgPlugin
        plugin = AddmsgPlugin()
        agent = _make_agent()
        session = self._make_session(agent)
        ctx = self._make_ctx(session, "")
        result = await plugin.async_execute(ctx)
        assert result is True
        assert agent.has_pending_addmsg() is False

    async def test_no_streaming_runs_round(self):
        from src.core.commands.plugins.addmsg_plugin import AddmsgPlugin
        plugin = AddmsgPlugin()
        agent = _make_agent()
        session = self._make_session(agent)
        ctx = self._make_ctx(session, "普通消息内容")
        run_round = AsyncMock(return_value={"interrupted": False, "pending": False})
        session.run_round = run_round
        session.save_checkpoint = MagicMock()
        session.run_pending_loop = AsyncMock(return_value=(False, []))
        plugin._loop = MagicMock()
        plugin._loop._chat_ui = None

        result = await plugin.async_execute(ctx)
        assert result is True
        run_round.assert_awaited_once_with("普通消息内容")
        session.save_checkpoint.assert_called_once()

    async def test_streaming_queues_addmsg(self):
        from src.core.commands.plugins.addmsg_plugin import AddmsgPlugin
        plugin = AddmsgPlugin()
        agent = _make_agent()
        session = self._make_session(agent, state="running")
        ctx = self._make_ctx(session, "流式补充")
        plugin._loop = MagicMock()
        plugin._loop._chat_ui = None

        result = await plugin.async_execute(ctx)
        assert result is True
        assert agent.drain_addmsg() == ["流式补充"]

    async def test_subagent_rejected(self):
        from src.core.commands.plugins.addmsg_plugin import AddmsgPlugin
        from src.core.subagent import SubAgent

        parent = MagicMock()
        parent.get_tool_registry.return_value = MagicMock()
        parent.model = "test-model"
        parent._event_port = MagicMock()
        parent._async_model_port = None
        parent._subagent_records = []
        prompt_builder = MagicMock()
        prompt_builder.build_execute_agent_system_prompt.return_value = ["system"]
        parent.get_prompt_builder_port.return_value = prompt_builder
        sub = SubAgent("agent-1", "desc", "prompt", parent)

        plugin = AddmsgPlugin()
        session = self._make_session(sub)
        ctx = self._make_ctx(session, "内容")
        plugin._loop = MagicMock()
        plugin._loop._chat_ui = None

        result = await plugin.async_execute(ctx)
        assert result is True
        assert sub.has_pending_addmsg() is False  # 未排队
        assert len(sub.messages) == 2  # 未新增用户消息
