"""Scope 作用域注册原语测试 — 按 key 划分注册空间（对应 dsh core/scope）。

覆盖：
- open / of / require / close / keys 生命周期；
- 作用域内注册随 close 全部撤销（可逆副作用）；
- 隔离 key 只解析本地注册，不穿透父级/内核；
- derive 继承父级解析、isolate 追加隔离；
- plugin 在作用域内挂载继承该作用域解析；
- 与 Kernel 集成：根上下文为父级，作用域不影响内核全局服务。
"""

from __future__ import annotations

import pytest

from src.kernel import Kernel, Scope, ScopeRegistry, plugin


async def test_open_returns_scope_and_registers():
    kernel = Kernel(name="scope-registry")
    registry = ScopeRegistry(kernel.root)
    scope = registry.open("agent-1")
    assert isinstance(scope, Scope)
    assert registry.of("agent-1") is scope
    assert "agent-1" in registry
    assert registry.keys() == ["agent-1"]


async def test_open_same_key_returns_existing():
    kernel = Kernel(name="scope-registry")
    registry = ScopeRegistry(kernel.root)
    first = registry.open("agent-1")
    second = registry.open("agent-1")
    assert first is second


async def test_open_rejects_empty_key():
    kernel = Kernel(name="scope-registry")
    registry = ScopeRegistry(kernel.root)
    with pytest.raises(ValueError):
        registry.open("")


async def test_provide_visible_only_inside_scope():
    kernel = Kernel(name="scope-registry")
    registry = ScopeRegistry(kernel.root)
    scope = registry.open("agent-1")
    scope.provide("persona", "reviewer")
    assert scope.service("persona") == "reviewer"
    assert not kernel.has_service("persona")
    assert kernel.root.service("persona") is None


async def test_close_revokes_registrations():
    kernel = Kernel(name="scope-registry")
    registry = ScopeRegistry(kernel.root)
    scope = registry.open("agent-1")
    scope.provide("persona", "reviewer")
    assert scope.closed is False
    assert registry.close("agent-1") is True
    assert scope.closed is True
    assert scope.service("persona") is None
    assert registry.of("agent-1") is None


async def test_close_all_closes_every_scope():
    kernel = Kernel(name="scope-registry")
    registry = ScopeRegistry(kernel.root)
    a = registry.open("a")
    b = registry.open("b")
    registry.close_all()
    assert a.closed and b.closed
    assert registry.keys() == []


async def test_isolate_does_not_fall_through_to_kernel():
    kernel = Kernel(name="scope-registry")

    @plugin("base")
    def base(ctx):
        ctx.provide("llm", "global-llm")

    kernel.plugin(base)
    await kernel.settle()

    registry = ScopeRegistry(kernel.root)
    scope = registry.open("agent-1", isolated=["llm"])
    assert scope.has("llm") is False
    scope.provide("llm", "scoped-llm")
    assert scope.service("llm") == "scoped-llm"
    assert kernel.resolve_service("llm") == "global-llm"


async def test_scope_inherits_non_isolated_services():
    kernel = Kernel(name="scope-registry")

    @plugin("base")
    def base(ctx):
        ctx.provide("tools", "shared-tools")

    kernel.plugin(base)
    await kernel.settle()

    registry = ScopeRegistry(kernel.root)
    scope = registry.open("agent-1", isolated=["llm"])
    assert scope.service("tools") == "shared-tools"


async def test_derive_inherits_scope_registrations():
    kernel = Kernel(name="scope-registry")
    registry = ScopeRegistry(kernel.root)
    scope = registry.open("agent-1")
    scope.provide("persona", "reviewer")
    child = scope.derive()
    assert child.service("persona") == "reviewer"
    child.provide("persona", "coder")
    assert child.service("persona") == "coder"
    assert scope.service("persona") == "reviewer"


async def test_isolate_appends_to_existing_isolation():
    kernel = Kernel(name="scope-registry")

    @plugin("base")
    def base(ctx):
        ctx.provide("llm", "global-llm")
        ctx.provide("fs", "global-fs")

    kernel.plugin(base)
    await kernel.settle()

    registry = ScopeRegistry(kernel.root)
    scope = registry.open("agent-1", isolated=["llm"])
    child = scope.isolate("fs")
    assert child.has("llm") is False
    assert child.has("fs") is False
    assert scope.has("fs") is True


async def test_effect_revoked_on_close():
    kernel = Kernel(name="scope-registry")
    registry = ScopeRegistry(kernel.root)
    scope = registry.open("agent-1")
    state = {"closed": False}
    scope.effect(lambda: (lambda: state.__setitem__("closed", True)))
    registry.close("agent-1")
    assert state["closed"] is True


async def test_on_listener_removed_on_close():
    kernel = Kernel(name="scope-registry")
    registry = ScopeRegistry(kernel.root)
    scope = registry.open("agent-1")
    seen = []

    async def handler(value):
        seen.append(value)

    scope.on("demo/event", handler)
    assert kernel.bus.listener_count("demo/event") == 1
    registry.close("agent-1")
    assert kernel.bus.listener_count("demo/event") == 0


async def test_plugin_under_scope_sees_scoped_service():
    kernel = Kernel(name="scope-registry")

    @plugin("base")
    def base(ctx):
        ctx.provide("llm", "global-llm")

    kernel.plugin(base)
    await kernel.settle()

    registry = ScopeRegistry(kernel.root)
    scope = registry.open("agent-1", isolated=["llm"])
    scope.provide("llm", "scoped-llm")
    seen = {}

    @plugin("reader")
    def reader(ctx):
        seen["llm"] = ctx.llm

    scope.plugin(reader)
    await kernel.settle()
    assert seen["llm"] == "scoped-llm"
    assert kernel.resolve_service("llm") == "global-llm"


async def test_closed_scope_rejects_provide():
    kernel = Kernel(name="scope-registry")
    registry = ScopeRegistry(kernel.root)
    scope = registry.open("agent-1")
    registry.close("agent-1")
    with pytest.raises(RuntimeError):
        scope.provide("x", 1)


async def test_context_manager_closes_scope():
    kernel = Kernel(name="scope-registry")
    registry = ScopeRegistry(kernel.root)
    with registry.open("agent-1") as scope:
        scope.provide("x", 1)
        assert scope.service("x") == 1
    assert scope.closed is True
    assert registry.of("agent-1") is None


async def test_require_missing_raises():
    kernel = Kernel(name="scope-registry")
    registry = ScopeRegistry(kernel.root)
    with pytest.raises(KeyError):
        registry.require("ghost")


async def test_parent_scope_via_registry_open():
    kernel = Kernel(name="scope-registry")
    registry = ScopeRegistry(kernel.root)
    parent = registry.open("agent-1")
    parent.provide("persona", "reviewer")
    child = registry.open("agent-1-sub", parent=parent)
    assert child.service("persona") == "reviewer"
