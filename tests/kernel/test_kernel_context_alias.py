"""Context 服务命名别名测试 — snake_case ↔ camelCase（对齐 dsh ctx 键）。

DeepSeek Harness 的 ctx 键采用 camelCase（``agentLoop`` / ``sessionProjections``），
本项目内部服务 key 采用 snake_case（``agent_loop`` / ``session_projections``）。
解析服务时未命中则尝试另一种命名形态，两种写法均可访问同一服务。
"""

from __future__ import annotations

import pytest

from src.kernel import Kernel, plugin
from src.kernel.context import _alternate_service_key


def test_alternate_key_snake_to_camel():
    assert _alternate_service_key("agent_loop") == "agentLoop"
    assert _alternate_service_key("session_projections") == "sessionProjections"
    assert _alternate_service_key("fs") == ""


def test_alternate_key_camel_to_snake():
    assert _alternate_service_key("agentLoop") == "agent_loop"
    assert _alternate_service_key("sessionProjections") == "session_projections"


async def test_context_resolves_camel_case_alias():
    kernel = Kernel(name="alias")

    @plugin("svc")
    def svc(ctx):
        ctx.provide("agent_loop", "loop-service")

    kernel.plugin(svc)
    await kernel.settle()
    assert kernel.root.agentLoop == "loop-service"
    assert kernel.root.service("agentLoop") == "loop-service"
    assert kernel.root.service("agent_loop") == "loop-service"


async def test_context_resolves_snake_case_when_registered_camel():
    kernel = Kernel(name="alias")

    @plugin("svc")
    def svc(ctx):
        ctx.provide("sessionProjections", "proj")

    kernel.plugin(svc)
    await kernel.settle()
    assert kernel.root.session_projections == "proj"
    assert kernel.root.sessionProjections == "proj"


async def test_missing_alias_still_raises():
    kernel = Kernel(name="alias")
    with pytest.raises(AttributeError):
        _ = kernel.root.definitelyMissing
