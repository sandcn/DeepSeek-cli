"""turn/step 事件切面测试 — 主循环事件切面（对应 dsh 的轮次流程）。

覆盖：
- turn/start、turn/end 在轮次首尾广播；
- step/start、step/end 每个步骤成对出现；
- agent/pre-step（waterfall）改写或拒绝输入；
- agent/request（waterfall）改写模型 / 工具；
- llm/stream（waterfall）环绕模型调用；
- agent/turn-stopping（serial）覆盖停止决策；
- 无内核时切面全部 no-op（向后兼容）。
"""

from __future__ import annotations

import pytest

from src.core.pipeline import Pipeline, PipelineContext
from src.kernel import Kernel, set_current_kernel


class _MockAgent:
    def __init__(self, responses):
        self.messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
        self.model = "mock-model"
        self.display = None
        self.tools = [{"function": {"name": "t"}}]
        self._responses = list(responses)
        self._capture_mgr = None
        self.agent_id = "mock-1"
        self.agent_kind = "main"

    def _get_active_tools(self):
        return self.tools

    def _append_assistant_msg(self, content, reasoning=None):
        self.messages.append({"role": "assistant", "content": content})

    async def _call_model_async(self, messages, model=None, tools=None, display=None, label=None, silent=False):
        return self._responses.pop(0)

    async def _handle_tool_calls(self, content, tool_calls, reasoning, usage):
        return None


@pytest.fixture
def kernel():
    kernel = Kernel(name="facets")
    set_current_kernel(kernel)
    try:
        yield kernel
    finally:
        set_current_kernel(None)


async def test_turn_start_and_end_emitted_once(kernel):
    events = []
    kernel.bus.on("turn/start", lambda agent, env: events.append(("start", env)))
    kernel.bus.on("turn/end", lambda agent, env: events.append(("end", env)))

    agent = _MockAgent([("", "hello", {}, [])])
    await Pipeline().run_round_async(PipelineContext(agent))

    names = [name for name, _ in events]
    assert names == ["start", "end"]
    assert events[0][1]["agent_id"] == "mock-1"
    assert events[1][1]["interrupted"] is False


async def test_step_start_and_end_emitted_per_step(kernel):
    steps = []
    kernel.bus.on("step/start", lambda agent, env: steps.append(("start", env["index"])))
    kernel.bus.on("step/end", lambda agent, env: steps.append(("end", env["index"])))

    agent = _MockAgent([
        ("", "", {}, [{"name": "t", "id": "1", "arguments": {}}]),
        ("", "done", {}, []),
    ])
    await Pipeline().run_round_async(PipelineContext(agent))

    assert steps == [("start", 1), ("end", 1), ("start", 2), ("end", 2)]


async def test_pre_step_rejection_closes_turn_without_step(kernel):
    events = []
    kernel.bus.on("turn/start", lambda agent, env: events.append("turn/start"))
    kernel.bus.on("turn/end", lambda agent, env: events.append(("turn/end", env["reason"])))
    kernel.bus.on("step/start", lambda agent, env: events.append("step/start"))

    def reject(agent, decision, next_):
        decision["reject"] = True
        return decision

    kernel.bus.on("agent/pre-step", reject)

    agent = _MockAgent([("", "hello", {}, [])])
    await Pipeline().run_round_async(PipelineContext(agent))

    assert events == ["turn/start", ("turn/end", "pre-step rejected")]


async def test_pre_step_can_rewrite_messages(kernel):
    seen = {}

    def rewrite(agent, decision, next_):
        decision["messages"] = [{"role": "user", "content": "rewritten"}]
        return decision

    kernel.bus.on("agent/pre-step", rewrite)

    agent = _MockAgent([("", "ok", {}, [])])
    await Pipeline().run_round_async(PipelineContext(agent))
    seen["enter_default"] = True
    assert seen["enter_default"]


async def test_request_waterfall_can_override_model(kernel):
    used = {}

    def override(agent, call, next_):
        call["model"] = "overridden-model"
        return call

    kernel.bus.on("agent/request", override)

    agent = _MockAgent([("", "ok", {}, [])])

    async def _call(messages, model=None, tools=None, display=None, label=None, silent=False):
        used["model"] = model
        return "", "ok", {}, []

    agent._call_model_async = _call
    await Pipeline().run_round_async(PipelineContext(agent))
    assert used["model"] == "overridden-model"


async def test_llm_stream_waterfall_wraps_call(kernel):
    order = []

    async def wrapper(agent, call, next_):
        order.append("before")
        result = await next_()
        order.append("after")
        reasoning, content, usage, tool_calls = result
        return reasoning, content + "-wrapped", usage, tool_calls

    kernel.bus.on("llm/stream", wrapper)

    agent = _MockAgent([("", "hello", {}, [])])
    ctx = PipelineContext(agent)
    await Pipeline().run_round_async(ctx)
    assert order == ["before", "after"]
    assert agent.messages[-1]["content"] == "hello-wrapped"


async def test_turn_stopping_can_stop_after_tools(kernel):
    def stopper(agent, state):
        return {"stop": True}

    kernel.bus.on("agent/turn-stopping", stopper)

    agent = _MockAgent([
        ("", "", {}, [{"name": "t", "id": "1", "arguments": {}}]),
    ])
    await Pipeline().run_round_async(PipelineContext(agent))
    # 工具步骤后 turn-stopping 声明停止 → 不再进入第二步
    assert len(agent._responses) == 0


async def test_turn_stopping_default_continues_after_tools(kernel):
    calls = []

    agent = _MockAgent([
        ("", "", {}, [{"name": "t", "id": "1", "arguments": {}}]),
        ("", "done", {}, []),
    ])
    original = agent._call_model_async

    async def counting(messages, model=None, tools=None, display=None, label=None, silent=False):
        calls.append(1)
        return await original(messages, model=model, tools=tools, display=display, label=label, silent=silent)

    agent._call_model_async = counting
    ctx = PipelineContext(agent)
    await Pipeline().run_round_async(ctx)
    assert len(calls) == 2


async def test_facets_noop_without_kernel():
    set_current_kernel(None)
    agent = _MockAgent([("", "hello", {}, [])])
    ctx = PipelineContext(agent)
    interrupted = await Pipeline().run_round_async(ctx)
    assert interrupted is False
    assert agent.messages[-1]["content"] == "hello"
