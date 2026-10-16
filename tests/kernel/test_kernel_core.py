"""内核核心测试 — Fiber 状态机、依赖驱动加载/卸载、可逆副作用。

覆盖「一切皆插件」的运行时语义：
- 插件依赖未就绪时停在 PENDING；
- 依赖就绪后自动加载，服务消失后自动卸载，服务恢复后重新加载；
- ctx.effect / ctx.on / ctx.provide 的注册随 Fiber 卸载全部撤销；
- 手动 dispose 后不再重载；内核 dispose 递归清理。
"""

from __future__ import annotations

import asyncio

import pytest

from src.kernel import FiberState, Kernel, Service, plugin


@pytest.fixture
def kernel():
    return Kernel(name="test")


def test_fiber_state_machine_enum():
    assert FiberState.PENDING.value == "PENDING"
    assert {s.value for s in FiberState} == {
        "PENDING", "LOADING", "ACTIVE", "FAILED", "UNLOADING", "DISPOSED"
    }


async def test_plugin_starts_active(kernel):
    @plugin("solo")
    def apply(ctx):
        ctx.provide("solo", 1)

    fiber = kernel.plugin(apply)
    await kernel.settle()
    assert fiber.state is FiberState.ACTIVE
    assert kernel.resolve_service("solo") == 1


async def test_dependency_driven_load_and_reload(kernel):
    events = []

    @plugin("dep")
    def dep(ctx):
        ctx.provide("dep", "D")

    @plugin("consumer", inject=["dep"])
    def consumer(ctx):
        events.append("start")
        ctx.effect(lambda: (lambda: events.append("stop")))

    dep_fiber = kernel.plugin(dep)
    consumer_fiber = kernel.plugin(consumer)

    await kernel.settle()
    assert events == ["start"]

    await dep_fiber.dispose()
    await kernel.settle()
    assert consumer_fiber.state is FiberState.DISPOSED
    assert events == ["start", "stop"]

    kernel.plugin(dep)
    await kernel.settle()
    assert consumer_fiber.state is FiberState.ACTIVE
    assert events == ["start", "stop", "start"]


async def test_pending_until_dependency_available(kernel):
    @plugin("late")
    def late(ctx):
        ctx.provide("late", True)

    @plugin("waiter", inject=["late"])
    def waiter(ctx):
        ctx.provide("waiter", True)

    waiter_fiber = kernel.plugin(waiter)
    await kernel.settle()
    assert waiter_fiber.state is FiberState.PENDING

    kernel.plugin(late)
    await kernel.settle()
    assert waiter_fiber.state is FiberState.ACTIVE


async def test_missing_dependencies_reported(kernel):
    @plugin("needs", inject=["a", "b"])
    def needs(ctx):
        pass

    fiber = kernel.plugin(needs)
    await kernel.settle()
    assert fiber.missing_dependencies() == ["a", "b"]


async def test_service_stack_override_and_release(kernel):
    @plugin("one")
    def one(ctx):
        ctx.provide("x", "one")

    @plugin("two")
    def two(ctx):
        ctx.provide("x", "two")

    f1 = kernel.plugin(one)
    await kernel.settle()
    assert kernel.resolve_service("x") == "one"

    f2 = kernel.plugin(two)
    await kernel.settle()
    assert kernel.resolve_service("x") == "two"

    await f2.dispose()
    await kernel.settle()
    assert kernel.resolve_service("x") == "one"
    await f1.dispose()


async def test_manual_dispose_does_not_reload(kernel):
    @plugin("keep")
    def keep(ctx):
        ctx.provide("keep", 1)

    fiber = kernel.plugin(keep)
    await kernel.settle()
    await fiber.dispose()
    await kernel.settle()
    assert fiber.state is FiberState.DISPOSED
    assert not kernel.has_service("keep")


async def test_failed_plugin_transitions_to_failed(kernel):
    @plugin("boom")
    def boom(ctx):
        raise RuntimeError("bang")

    fiber = kernel.plugin(boom)
    await kernel.settle()
    assert fiber.state is FiberState.FAILED
    assert isinstance(fiber.error, RuntimeError)


async def test_effect_disposer_order_reversed(kernel):
    order = []

    @plugin("multi")
    def multi(ctx):
        ctx.effect(lambda: (lambda: order.append("first")))
        ctx.effect(lambda: (lambda: order.append("second")))

    fiber = kernel.plugin(multi)
    await kernel.settle()
    await fiber.dispose()
    assert order == ["second", "first"]


async def test_disposer_runs_once(kernel):
    calls = {"n": 0}

    @plugin("once")
    def once(ctx):
        ctx.effect(lambda: (lambda: calls.__setitem__("n", calls["n"] + 1)))

    fiber = kernel.plugin(once)
    await kernel.settle()
    await fiber.dispose()
    await fiber.dispose()
    assert calls["n"] == 1


async def test_async_effect_cleanup(kernel):
    events = []

    async def _cleanup():
        events.append("async-cleanup")

    @plugin("async-effect")
    def apply(ctx):
        ctx.effect(lambda: _cleanup)

    fiber = kernel.plugin(apply)
    await kernel.settle()
    await fiber.dispose()
    assert events == ["async-cleanup"]


async def test_context_attribute_service_access(kernel):
    @plugin("ctx-svc")
    def apply(ctx):
        ctx.provide("greeting", "hi")

    kernel.plugin(apply)
    await kernel.settle()
    assert kernel.root.greeting == "hi"
    with pytest.raises(AttributeError):
        _ = kernel.root.nonexistent


async def test_nested_child_plugin_disposed_with_parent(kernel):
    child_state = []

    @plugin("child")
    def child(ctx):
        child_state.append("child-start")
        ctx.effect(lambda: (lambda: child_state.append("child-stop")))

    @plugin("parent")
    def parent(ctx):
        ctx.plugin(child)

    parent_fiber = kernel.plugin(parent)
    await kernel.settle()
    assert child_state == ["child-start"]
    await parent_fiber.dispose()
    assert "child-stop" in child_state


async def test_service_inject_dynamic(kernel):
    @plugin("dynamic", inject=["later"])
    def dynamic(ctx):
        ctx.provide("dynamic", True)

    @plugin("provider")
    def provider(ctx):
        ctx.provide("later", True)

    fiber = kernel.plugin(dynamic)
    await kernel.settle()
    assert fiber.state is FiberState.PENDING
    kernel.plugin(provider)
    await kernel.settle()
    assert fiber.state is FiberState.ACTIVE


async def test_context_inject_runtime(kernel):
    @plugin("runtime-inject")
    def apply(ctx):
        ctx.inject("needed")
        ctx.provide("runtime_inject", True)

    fiber = kernel.plugin(apply)
    await kernel.settle()
    assert fiber.missing_dependencies() == ["needed"]
    assert fiber.state is FiberState.PENDING

    @plugin("needed-provider")
    def provider(ctx):
        ctx.provide("needed", True)

    kernel.plugin(provider)
    await kernel.settle()
    assert fiber.state is FiberState.ACTIVE


async def test_kernel_dispose_clears_all(kernel):
    @plugin("a")
    def a(ctx):
        ctx.provide("a", 1)

    kernel.plugin(a)
    await kernel.settle()
    await kernel.dispose()
    assert kernel.service_keys() == []
    assert kernel.fibers() == []


async def test_service_class_plugin(kernel):
    class MyService(Service):
        provide = "svc"
        name = "svc"

        def ping(self):
            return "pong"

    @plugin("svc-plugin", provide=["svc"])
    def apply(ctx):
        return MyService(ctx)

    kernel.plugin(apply)
    await kernel.settle()
    assert kernel.resolve_service("svc").ping() == "pong"


async def test_fiber_restart_reinitializes(kernel):
    starts = []

    @plugin("restartable")
    def apply(ctx):
        starts.append("start")
        ctx.effect(lambda: (lambda: starts.append("stop")))

    fiber = kernel.plugin(apply)
    await kernel.settle()
    assert starts == ["start"]

    await fiber.restart()
    assert starts == ["start", "stop", "start"]
    assert fiber.state is FiberState.ACTIVE


async def test_kernel_reload_by_name(kernel):
    counter = {"n": 0}

    @plugin("counted")
    def apply(ctx):
        counter["n"] += 1

    kernel.plugin(apply)
    await kernel.settle()
    assert counter["n"] == 1

    await kernel.reload("counted")
    assert counter["n"] == 2


async def test_kernel_reload_unknown_raises(kernel):
    from src.kernel import PluginError

    await kernel.settle()
    with pytest.raises(PluginError):
        await kernel.reload("ghost")


async def test_kernel_mount_file(tmp_path, kernel):
    path = tmp_path / "hot.py"
    path.write_text(
        "from src.kernel import plugin\n"
        "@plugin('from-file')\n"
        "def apply(ctx):\n"
        "    ctx.provide('from_file', 'ok')\n",
        encoding="utf-8",
    )
    fiber = kernel.mount_file(str(path))
    await kernel.settle()
    assert fiber.state is FiberState.ACTIVE
    assert kernel.resolve_service("from_file") == "ok"


async def test_kernel_mount_file_all_multi_plugins(tmp_path, kernel):
    path = tmp_path / "multi.py"
    path.write_text(
        "from src.kernel import plugin\n"
        "@plugin('m1')\n"
        "def apply_m1(ctx):\n"
        "    ctx.provide('m1', 1)\n"
        "@plugin('m2')\n"
        "def apply_m2(ctx):\n"
        "    ctx.provide('m2', 2)\n",
        encoding="utf-8",
    )
    fibers = kernel.mount_file_all(str(path))
    await kernel.settle()
    assert len(fibers) == 2
    assert kernel.resolve_service("m1") == 1
    assert kernel.resolve_service("m2") == 2
