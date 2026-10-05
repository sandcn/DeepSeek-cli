"""工具执行管线与工具插件化测试。

覆盖「一切皆插件」的工具侧：
- ``tools/pre-execute`` / ``tools/execute`` / ``tools/post-execute`` waterfall 钩子；
- 策略插件经 pre-execute 拒绝被排除工具 / plan 路径白名单；
- 内置工具由 ``tools_builtin`` 插件显式注册；
- 外部插件经 ``ctx.tools.register`` / ``ctx.tools.define`` 注册工具；
- ``tool_plugin`` 清单式工具声明。
"""

from __future__ import annotations

import pytest

from src.core.tool_executor_async import ToolScheduler
from src.plugins.bootstrap import build_kernel, shutdown_kernel


@pytest.fixture
async def cli_kernel():
    kernel = await build_kernel("cli")
    try:
        yield kernel
    finally:
        await shutdown_kernel(kernel)


class _AgentStub:
    def __init__(self, agent_type=None):
        self.agent_type = agent_type


async def test_tools_builtin_registers_all(cli_kernel):
    tools = cli_kernel.resolve_service("tools")
    names = tools.names()
    assert "read_file" in names and "bash" in names and "rm" in names
    assert len(names) >= 16


async def test_pre_execute_deny_skips_execution(cli_kernel):
    calls = []

    async def deny(call, next_):
        calls.append(call["name"])
        return {"allow": False, "reason": "被测试策略拒绝"}

    cli_kernel.root.on("tools/pre-execute", deny)
    scheduler = ToolScheduler()
    tc = {"id": "t1", "name": "read_file", "arguments": {}}
    _id, output, success = await scheduler._execute_one_async(
        tc, agent_ref=_AgentStub(), on_before=None, on_after=None, run_method=None
    )
    assert success is False
    assert "被测试策略拒绝" in output
    assert calls == ["read_file"]


async def test_pre_execute_allow_runs_tool(cli_kernel):
    seen = []

    async def allow(call, next_):
        seen.append(call["name"])
        return await next_()

    cli_kernel.root.on("tools/pre-execute", allow)
    scheduler = ToolScheduler()
    tc = {"id": "t2", "name": "read_file", "arguments": {"path": "README.md"}}
    _id, output, success = await scheduler._execute_one_async(
        tc, agent_ref=_AgentStub(), on_before=None, on_after=None, run_method=None
    )
    assert success is True
    assert seen == ["read_file"]


async def test_post_execute_rewrites_output(cli_kernel):
    async def rewrite(call, output, next_):
        value = await next_()
        return f"[rewritten]{value}"

    cli_kernel.root.on("tools/post-execute", rewrite)
    scheduler = ToolScheduler()
    tc = {"id": "t3", "name": "read_file", "arguments": {"path": "README.md"}}
    _id, output, success = await scheduler._execute_one_async(
        tc, agent_ref=_AgentStub(), on_before=None, on_after=None, run_method=None
    )
    assert success is True
    assert str(output).startswith("[rewritten]")


async def test_policy_denies_excluded_tool_for_agent_type(cli_kernel):
    scheduler = ToolScheduler()
    tc = {"id": "t4", "name": "bash", "arguments": {"command": "echo hi"}}
    _id, output, success = await scheduler._execute_one_async(
        tc, agent_ref=_AgentStub(agent_type="review"),
        on_before=None, on_after=None, run_method=None,
    )
    assert success is False
    assert "review" in str(output)


async def test_policy_denies_plan_path_outside_whitelist(cli_kernel):
    scheduler = ToolScheduler()
    tc = {
        "id": "t5",
        "name": "write_file",
        "arguments": {"path": "src/evil.py", "content": "x"},
    }
    _id, output, success = await scheduler._execute_one_async(
        tc, agent_ref=_AgentStub(agent_type="plan"),
        on_before=None, on_after=None, run_method=None,
    )
    assert success is False
    assert "plan agent" in str(output)


async def test_plugin_define_tool_executes(cli_kernel):
    async def echo_handler(text: str = ""):
        return f"echo:{text}"

    tool_class = cli_kernel.root.tools.define(
        "test_echo",
        {
            "type": "function",
            "function": {
                "name": "test_echo",
                "description": "echo",
                "parameters": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                },
            },
        },
        echo_handler,
    )
    assert tool_class.name == "test_echo"
    assert "test_echo" in cli_kernel.root.tools.names()

    scheduler = ToolScheduler()
    tc = {"id": "t6", "name": "test_echo", "arguments": {"text": "hi"}}
    _id, output, success = await scheduler._execute_one_async(
        tc, agent_ref=_AgentStub(), on_before=None, on_after=None, run_method=None
    )
    assert success is True
    assert output == "echo:hi"


async def test_tool_plugin_manifest_declaration(cli_kernel):
    from src.plugins.tool_plugin import apply as tool_plugin

    fiber = cli_kernel.plugin(tool_plugin, config={"tool": "src.tools.read_file.ReadFileFunc"})
    await cli_kernel.settle()
    assert fiber.state.value == "ACTIVE"
    assert "read_file" in cli_kernel.root.tools.names()
    await fiber.dispose()
    await cli_kernel.settle()


async def test_tools_on_register_listener(cli_kernel):
    from src.tools.base import Func, tool_metadata

    seen = []

    @tool_metadata(parallel_safe=True)
    class DummyTool(Func):
        name = "dummy_listener_tool"

        @classmethod
        def to_tool_schema(cls):
            return {"type": "function", "function": {"name": cls.name}}

        async def execute(self):
            return "ok"

    dispose = cli_kernel.root.tools.on_register(lambda name, cls: seen.append(name))
    cli_kernel.root.tools.register(DummyTool)
    assert "dummy_listener_tool" in seen
    dispose()
    cli_kernel.root.tools.unregister("dummy_listener_tool")
    assert "dummy_listener_tool" not in cli_kernel.root.tools.names()
