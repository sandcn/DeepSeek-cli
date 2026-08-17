"""/addmsg 工具调用场景端到端测试（复现：工具调用完成后不插入用户消息）

覆盖场景：
1. 工具执行期间用户输入 /addmsg → 工具完成后插入（期望位置：tool 结果之后）
2. 模型返回工具调用时用户已输入 /addmsg → 插入位置必须合法
   （assistant(tool_calls) 与 tool 结果之间不得插入 user 消息，否则 API 400）
3. 多工具链：每轮工具完成都应触发插入检查
4. 工具调用完成后、下一轮模型调用期间输入 /addmsg → after_model_call 捕获插入
5. addmsg 插入后工具链继续 → 消息必须在后续模型调用中被处理
6. 流式期间经 Input 捕获路径的完整插入
7. 非 /addmsg 的排队输入必须保留（走原 queued_input 路径）
"""
from __future__ import annotations

import asyncio
import pytest

from src.core.adapters.interrupt import MockInterruptAdapter
from src.core.adapters.null import _NullDisplayPort, _NullEventPort, _NullOutputPort
from src.core.agent import Agent
from src.core.middleware.interrupt import _InterruptCheckMiddleware
from src.core.pipeline import PipelineContext


def _make_agent() -> Agent:
    agent = Agent(
        model="test-model",
        display_port=_NullDisplayPort(),
        event_port=_NullEventPort(),
        output_port=_NullOutputPort(),
    )
    agent._interrupt_port = MockInterruptAdapter()
    for i, mw in enumerate(agent.pipeline._async_middlewares):
        if mw.name == "InterruptCheck":
            agent.pipeline._async_middlewares[i] = _InterruptCheckMiddleware(
                MockInterruptAdapter()
            )
    return agent


class _FakeInput:
    """模拟 Input 的 peek/get 行为。"""

    def __init__(self, queued=None):
        self._queued = queued

    def peek_queued_input(self):
        return self._queued

    def get_queued_input(self):
        q = self._queued
        self._queued = None
        return q


def _assert_tool_chain_valid(messages) -> None:
    """校验 assistant(tool_calls) 与其 tool 结果之间没有插入其他角色消息。

    OpenAI/DeepSeek 兼容 API：assistant 携带 tool_calls 后，下一条消息必须
    是对应 tool_call 的 tool 结果（中间插入 user 会返回 400）。
    """
    for i, m in enumerate(messages):
        if m.get("role") == "assistant" and m.get("tool_calls"):
            nxt = messages[i + 1] if i + 1 < len(messages) else None
            assert nxt is not None, f"assistant tool_calls 后缺少 tool 消息: {m}"
            assert nxt.get("role") == "tool", (
                f"assistant tool_calls 后必须紧跟 tool 消息，实际是 {nxt.get('role')}: {m}"
            )


class TestAddmsgAfterToolExecution:
    """工具执行完成后插入 addmsg 的核心场景。"""

    async def test_input_addmsg_during_tool_inserted_after_tool(self):
        """工具执行期间用户在输入框输入 /addmsg，工具完成后必须插入。"""
        agent = _make_agent()
        agent.messages.append({"role": "user", "content": "原始问题"})
        # 注入 input provider：模拟用户在工具执行期间已输入 /addmsg 并回车
        fake_input = _FakeInput("/addmsg 工具后补充")
        agent.set_addmsg_input_provider(lambda: fake_input)

        calls = []
        tool_handled = []

        async def fake_call(messages, model=None, tools=None, display=None,
                            label=None, silent=False):
            calls.append(list(messages))
            if len(calls) == 1:
                return "", "", {"input": 0, "output": 0, "calls": 1}, [
                    {"id": "call_1", "name": "bash", "arguments": "{}"}
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
        # /addmsg 输入已被消费（不会残留到下一轮被当作普通消息）
        assert fake_input._queued is None
        # 消息已插入
        assert any(
            m.get("role") == "user" and m.get("content") == "工具后补充"
            for m in agent.messages
        )
        # 插入位置必须合法：assistant(tool_calls) 后紧跟 tool 结果
        _assert_tool_chain_valid(agent.messages)
        # 第二次模型调用携带插入的 user 消息
        assert any(
            m.get("role") == "user" and m.get("content") == "工具后补充"
            for m in calls[1]
        )

    async def test_addmsg_queued_before_tool_call_not_break_chain(self):
        """addmsg 在模型返回工具调用时已排队 → 插入不得破坏 tool 消息链。

        回归：修复前 after_model_call 无条件插入，把 user 消息塞进
        assistant(tool_calls) 与 tool 结果之间 → API 400 → 工具链断裂。
        """
        agent = _make_agent()
        agent.messages.append({"role": "user", "content": "原始问题"})
        agent._addmsg_queue = ["模型返回工具时排队"]
        calls = []
        tool_handled = []

        async def fake_call(messages, model=None, tools=None, display=None,
                            label=None, silent=False):
            calls.append(list(messages))
            if len(calls) == 1:
                return "", "", {"input": 0, "output": 0, "calls": 1}, [
                    {"id": "call_1", "name": "bash", "arguments": "{}"}
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
        # tool 消息链完整：assistant(tool_calls) → tool 结果 紧邻
        _assert_tool_chain_valid(agent.messages)
        # addmsg 消息确实插入（位于 tool 结果之后）
        assert any(
            m.get("role") == "user" and m.get("content") == "模型返回工具时排队"
            for m in agent.messages
        )
        # 第二次模型调用携带 addmsg 消息
        assert any(
            m.get("role") == "user" and m.get("content") == "模型返回工具时排队"
            for m in calls[1]
        )

    async def test_multiple_tool_rounds_insert_after_last_tool(self):
        """多轮工具调用：用户在工具执行期间输入 /addmsg → 最近一次工具完成后插入。

        时序模拟：
        - 模型调用 #1 返回工具 call_1 → after_model_call 无排队（未插入）
        - 工具 call_1 执行期间：用户输入 /addmsg（fake_input 置位）
        - after_tool_execution #1：捕获并插入（tool(call_1) 结果之后）
        - 模型调用 #2 携带 addmsg 继续 → 返回工具 call_2 …
        """
        agent = _make_agent()
        agent.messages.append({"role": "user", "content": "原始问题"})
        fake_input = _FakeInput(None)  # 初始无排队输入
        agent.set_addmsg_input_provider(lambda: fake_input)

        calls = []
        tool_handled = []

        async def fake_call(messages, model=None, tools=None, display=None,
                            label=None, silent=False):
            calls.append(list(messages))
            if len(calls) == 1:
                return "", "", {"input": 0, "output": 0, "calls": 1}, [
                    {"id": "call_1", "name": "bash", "arguments": "{}"}
                ]
            if len(calls) == 2:
                return "", "", {"input": 0, "output": 0, "calls": 1}, [
                    {"id": "call_2", "name": "read_file", "arguments": "{}"}
                ]
            return "", "最终回答", {"input": 0, "output": 0, "calls": 1}, []

        async def fake_handle_tool_calls(content, tool_calls, reasoning=None, usage=None):
            tool_handled.append(tool_calls)
            # 模拟：工具执行期间用户输入 /addmsg 并回车（此时进入 queued_input）
            if len(tool_handled) == 1:
                fake_input._queued = "/addmsg 第二轮工具后补充"
            for tc in tool_calls:
                agent._append_tool_result(tc["id"], f"{tc['name']} 结果")

        agent._call_model_async = fake_call
        agent._handle_tool_calls = fake_handle_tool_calls

        ctx = PipelineContext(agent)
        ctx.interrupt_port = MockInterruptAdapter()
        interrupted = await agent.pipeline.run_round_async(ctx)

        assert interrupted is False
        assert len(calls) == 3
        assert len(tool_handled) == 2
        assert fake_input._queued is None  # /addmsg 已被消费
        # 消息链完整
        _assert_tool_chain_valid(agent.messages)
        # addmsg 已插入
        assert any(
            m.get("role") == "user" and m.get("content") == "第二轮工具后补充"
            for m in agent.messages
        )
        # addmsg 消息必须位于第一个工具结果之后（不破坏链）
        idx_user = [i for i, m in enumerate(agent.messages)
                    if m.get("role") == "user" and m.get("content") == "第二轮工具后补充"][0]
        idx_tool1 = [i for i, m in enumerate(agent.messages)
                     if m.get("role") == "tool" and m.get("tool_call_id") == "call_1"][0]
        assert idx_user > idx_tool1, "addmsg 必须插入在工具结果之后"

    async def test_plain_input_not_consumed(self):
        """非 /addmsg 的排队输入必须保留（走原 queued_input 路径）。"""
        agent = _make_agent()
        agent.messages.append({"role": "user", "content": "原始问题"})
        fake_input = _FakeInput("普通消息不走 addmsg")
        agent.set_addmsg_input_provider(lambda: fake_input)

        calls = []

        async def fake_call(messages, model=None, tools=None, display=None,
                            label=None, silent=False):
            calls.append(list(messages))
            return "", "回答", {"input": 0, "output": 0, "calls": 1}, []

        agent._call_model_async = fake_call
        ctx = PipelineContext(agent)
        ctx.interrupt_port = MockInterruptAdapter()
        await agent.pipeline.run_round_async(ctx)

        # 普通消息未被消费，保留给 round_end
        assert fake_input._queued == "普通消息不走 addmsg"
        assert not any(
            m.get("role") == "user" and m.get("content") == "普通消息不走 addmsg"
            for m in agent.messages
        )

    async def test_addmsg_after_last_tool_during_next_model_call(self):
        """工具调用完成后、下一轮模型调用期间用户输入 /addmsg → 插入。

        复现用户报告场景（2026-08-17）：
        - 模型 #1 返回工具调用 A → 工具 A 执行完成（after_tool_execution 无排队）
        - 用户在模型 #2（处理工具结果）调用期间输入 /addmsg
        - 模型 #2 完成后 after_model_call 必须捕获并插入
        - 无工具调用 → addmsg_inserted 触发继续模型调用 #3 处理新消息
        """
        agent = _make_agent()
        agent.messages.append({"role": "user", "content": "原始问题"})
        from src.tui._input_buffer import InputBufferEditor
        input_ = InputBufferEditor(history_file=None)
        agent.set_addmsg_input_provider(lambda: input_)

        calls = []
        tool_handled = []

        async def fake_call(messages, model=None, tools=None, display=None,
                            label=None, silent=False):
            calls.append(list(messages))
            if len(calls) == 1:
                return "", "", {"input": 0, "output": 0, "calls": 1}, [
                    {"id": "call_1", "name": "bash", "arguments": "{}"}
                ]
            if len(calls) == 2:
                # 模拟：模型 #2 调用期间用户输入 /addmsg 并回车
                input_.set_buffer("/addmsg 工具完成后补充")
                input_._enter(append_history=lambda t: None)
                return "", "中间回答", {"input": 0, "output": 0, "calls": 1}, []
            return "", "最终回答", {"input": 0, "output": 0, "calls": 1}, []

        async def fake_handle_tool_calls(content, tool_calls, reasoning=None, usage=None):
            tool_handled.append(tool_calls)
            for tc in tool_calls:
                agent._append_tool_result(tc["id"], "工具结果")

        agent._call_model_async = fake_call
        agent._handle_tool_calls = fake_handle_tool_calls

        ctx = PipelineContext(agent)
        ctx.interrupt_port = MockInterruptAdapter()
        interrupted = await agent.pipeline.run_round_async(ctx)

        assert interrupted is False
        assert len(calls) == 3  # 工具调用 → 中间回答 → 处理 addmsg 的最终回答
        assert len(tool_handled) == 1
        # addmsg 已插入且工具链完整
        assert any(
            m.get("role") == "user" and m.get("content") == "工具完成后补充"
            for m in agent.messages
        )
        _assert_tool_chain_valid(agent.messages)
        # 处理 addmsg 的第三次模型调用携带新消息
        assert any(
            m.get("role") == "user" and m.get("content") == "工具完成后补充"
            for m in calls[2]
        )

    async def test_addmsg_queued_then_tool_chain_continues(self):
        """addmsg 插入后工具链继续：消息必须在后续模型调用中被处理。"""
        agent = _make_agent()
        agent.messages.append({"role": "user", "content": "原始问题"})
        fake_input = _FakeInput("/addmsg 中途改方案")
        agent.set_addmsg_input_provider(lambda: fake_input)

        calls = []
        tool_handled = []

        async def fake_call(messages, model=None, tools=None, display=None,
                            label=None, silent=False):
            calls.append(list(messages))
            if len(calls) == 1:
                return "", "", {"input": 0, "output": 0, "calls": 1}, [
                    {"id": "call_1", "name": "bash", "arguments": "{}"}
                ]
            if len(calls) == 2:
                # addmsg 在 after_model_call #1 已插入（assistant 未追加前）
                return "", "", {"input": 0, "output": 0, "calls": 1}, [
                    {"id": "call_2", "name": "read_file", "arguments": "{}"}
                ]
            return "", "最终回答", {"input": 0, "output": 0, "calls": 1}, []

        async def fake_handle_tool_calls(content, tool_calls, reasoning=None, usage=None):
            tool_handled.append(tool_calls)
            # 与真实 ToolCallbackChain 一致：先追加 assistant(tool_calls)
            agent._append_assistant_message(content, tool_calls, reasoning)
            for tc in tool_calls:
                agent._append_tool_result(tc["id"], "结果")

        agent._call_model_async = fake_call
        agent._handle_tool_calls = fake_handle_tool_calls

        ctx = PipelineContext(agent)
        ctx.interrupt_port = MockInterruptAdapter()
        interrupted = await agent.pipeline.run_round_async(ctx)

        assert interrupted is False
        assert len(calls) == 3
        assert len(tool_handled) == 2
        # 工具链完整（assistant tool_calls 与其 tool 结果紧邻）
        _assert_tool_chain_valid(agent.messages)
        # addmsg 已插入且第二次模型调用（携带工具结果）能同时看到它
        assert any(
            m.get("role") == "user" and m.get("content") == "中途改方案"
            for m in calls[1]
        )
        # 插入位置：addmsg 位于 assistant(tool_calls call_1) 之前（合法）
        idx_addmsg = [i for i, m in enumerate(agent.messages)
                      if m.get("role") == "user" and m.get("content") == "中途改方案"][0]
        idx_assistant1 = [i for i, m in enumerate(agent.messages)
                          if m.get("role") == "assistant" and m.get("tool_calls")][0]
        assert idx_addmsg < idx_assistant1, "addmsg 必须位于 assistant(tool_calls) 之前"

    async def test_tool_cancelled_skips_insert(self):
        """工具执行被取消（interrupted）后 after_tool_execution 跳过插入。

        回归（2026-08-17）：工具取消时 assistant(tool_calls) 可能已追加而
        tool 结果缺失（链断裂），再插入 user 消息会加剧断裂（API 400）。
        中断时跳过插入，排队消息保留（由 on_round_complete 兜底插入——
        pipeline 已结束无后续模型调用，不会触发 API 校验）。
        """
        from src.core.middleware.addmsg import AddmsgMiddleware
        agent = _make_agent()
        agent._addmsg_queue = ["工具取消排队"]
        ctx = PipelineContext(agent)
        ctx.interrupt_port = MockInterruptAdapter()
        ctx.interrupted = True  # 模拟工具执行被取消
        mw = AddmsgMiddleware()
        await mw.after_tool_execution(ctx)
        # 中断时跳过插入：消息未插入断裂链
        assert not any(
            m.get("role") == "user" and m.get("content") == "工具取消排队"
            for m in agent.messages
        )
        # 排队消息保留，由 on_round_complete 兜底
        assert agent.has_pending_addmsg() is True

    async def test_tool_interrupted_on_round_complete_flushes(self):
        """中断场景：on_round_complete 兜底插入残留排队消息（不丢失）。

        与 test_tool_cancelled_skips_insert 衔接：after_tool_execution 跳过
        插入后，on_round_complete 把残留消息插入对话（pipeline 已结束，
        消息供下一轮 run_round 的模型调用消费）。
        """
        from src.core.middleware.addmsg import AddmsgMiddleware
        agent = _make_agent()
        agent._addmsg_queue = ["中断残留"]
        ctx = PipelineContext(agent)
        ctx.interrupt_port = MockInterruptAdapter()
        mw = AddmsgMiddleware()
        await mw.on_round_complete(ctx)
        assert agent._addmsg_queue == []
        assert any(
            m.get("role") == "user" and m.get("content") == "中断残留"
            for m in agent.messages
        )
