"""内核作用域测试 — extend / isolate / intercept 空间可组合性。

对应 Cordis 的 spatial composability：同一份代码在不同子树里解析到不同
服务实现/配置，且作用域内的注册随 Fiber 卸载撤销。
"""

from __future__ import annotations

import pytest

from src.kernel import FiberState, Kernel, ServiceExists, plugin


@pytest.fixture
def kernel():
    return Kernel(name="scope-test")


async def test_extend_shadows_parent_without_polluting(kernel):
    @plugin("base")
    def base(ctx):
        ctx.provide("svc", "root")

    kernel.plugin(base)
    await kernel.settle()
    assert kernel.root.svc == "root"

    child = kernel.root.extend()
    child.provide("svc", "child")
    assert child.svc == "child"
    # 父级/内核不受影响
    assert kernel.root.svc == "root"
    assert kernel.resolve_service("svc") == "root"


async def test_extend_inherits_other_services(kernel):
    @plugin("base")
    def base(ctx):
        ctx.provide("a", 1)
        ctx.provide("b", 2)

    kernel.plugin(base)
    await kernel.settle()
    child = kernel.root.extend()
    child.provide("b", 20)
    assert child.a == 1
    assert child.b == 20


async def test_isolate_does_not_fall_through(kernel):
    @plugin("base")
    def base(ctx):
        ctx.provide("svc", "root")

    kernel.plugin(base)
    await kernel.settle()

    isolated = kernel.root.isolate("svc")
    assert not isolated.has("svc")
    with pytest.raises(Exception):
        _ = isolated.consume("svc")
    isolated.provide("svc", "isolated")
    assert isolated.svc == "isolated"
    assert kernel.root.svc == "root"


async def test_isolated_inherits_non_isolated_keys(kernel):
    @plugin("base")
    def base(ctx):
        ctx.provide("shared", "S")

    kernel.plugin(base)
    await kernel.settle()
    isolated = kernel.root.isolate("other")
    assert isolated.shared == "S"


async def test_intercept_wraps_resolution(kernel):
    @plugin("base")
    def base(ctx):
        ctx.provide("svc", 10)

    kernel.plugin(base)
    await kernel.settle()

    wrapped = kernel.root.intercept("svc", lambda v: v * 2)
    assert wrapped.svc == 20
    # 未拦截的 key 正常
    assert kernel.root.svc == 10


async def test_intercept_star_matches_all(kernel):
    @plugin("base")
    def base(ctx):
        ctx.provide("x", 1)
        ctx.provide("y", 2)

    kernel.plugin(base)
    await kernel.settle()
    wrapped = kernel.root.intercept("*", lambda v: v + 100)
    assert wrapped.x == 101
    assert wrapped.y == 102


async def test_scoped_duplicate_provide_raises(kernel):
    child = kernel.root.extend()
    child.provide("k", 1)
    with pytest.raises(ServiceExists):
        child.provide("k", 2)


async def test_root_duplicate_still_stacks(kernel):
    root = kernel.root
    root.provide("k", 1)
    root.provide("k", 2)
    assert kernel.resolve_service("k") == 2


async def test_scoped_services_released_on_fiber_dispose(kernel):
    holder = {}

    @plugin("owner")
    def owner(ctx):
        scope = ctx.extend()
        scope.provide("local", "L")
        holder["scope"] = scope

    fiber = kernel.plugin(owner)
    await kernel.settle()
    assert holder["scope"].local == "L"

    await fiber.dispose()
    assert not holder["scope"].has("local")


async def test_plugin_under_isolated_scope_sees_isolated_service(kernel):
    @plugin("base")
    def base(ctx):
        ctx.provide("svc", "root")

    kernel.plugin(base)
    await kernel.settle()

    isolated = kernel.root.isolate("svc")
    isolated.provide("svc", "scoped")

    seen = {}

    @plugin("reader")
    def reader(ctx):
        seen["value"] = ctx.svc

    isolated.plugin(reader)
    await kernel.settle()
    assert seen["value"] == "scoped"
    assert kernel.root.svc == "root"


async def test_isolate_rejects_empty_key(kernel):
    with pytest.raises(ValueError):
        kernel.root.isolate("")


async def test_service_helper_is_scoped(kernel):
    child = kernel.root.extend()
    child.provide("n", 5)
    assert child.service("n") == 5
    assert child.service("missing", 42) == 42
