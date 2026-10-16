"""运行时不变量测试 — ctx.invariants 自检插件树关系。"""

from __future__ import annotations

import logging

import pytest

from src.kernel import Kernel, get_current_kernel, plugin
from src.plugins.bootstrap import build_kernel, shutdown_kernel


async def test_invariants_service_registered():
    kernel = await build_kernel("cli")
    try:
        service = kernel.resolve_service("invariants")
        assert service is not None
        assert "services.keys_valid" in service.names()
        assert "singletons.kernel_source" in service.names()
        assert service.check() == []
    finally:
        await shutdown_kernel(kernel)


def test_invariant_registry_detects_failure():
    from src.kernel.invariants import InvariantRegistry

    registry = InvariantRegistry()
    registry.register("ok", lambda kernel: None)
    registry.register("bad", lambda kernel: "boom")
    failures = registry.check_all(object())
    assert failures == ["bad: boom"]


def test_invariant_registry_disposer():
    from src.kernel.invariants import InvariantRegistry

    registry = InvariantRegistry()
    dispose = registry.register("x", lambda kernel: None)
    assert registry.names() == ["x"]
    dispose()
    assert registry.names() == []


async def test_custom_invariant_registered_by_plugin():
    kernel = await build_kernel("minimal", discover_external=False)
    try:
        service = kernel.resolve_service("invariants")

        def _always_fail(kernel_obj):
            return "expected failure"

        service.register("custom.fail", _always_fail)
        assert "custom.fail" in service.names()
        assert any("custom.fail" in f for f in service.check())
        assert service.unregister("custom.fail") is True
        assert service.check() == []
    finally:
        await shutdown_kernel(kernel)


async def test_plugin_construction_activates_current_kernel():
    """插件 apply 期间内核被登记为进程级当前内核，结束后恢复原登记。"""
    kernel = Kernel(name="probe")
    seen = []

    @plugin("probe", provide=["probe"])
    def apply(ctx):
        seen.append(get_current_kernel())
        return object()

    kernel.mount(apply)
    assert get_current_kernel() is None
    await kernel.settle()
    assert seen == [kernel]
    assert get_current_kernel() is None


async def test_plugin_restart_activates_current_kernel():
    """未经 settle 的 restart 同样在构造期激活内核，结束后恢复。"""
    kernel = Kernel(name="probe")
    seen = []

    @plugin("probe", provide=["probe"])
    def apply(ctx):
        seen.append(get_current_kernel())
        return object()

    fiber = kernel.mount(apply)
    await kernel.settle()
    seen.clear()
    await fiber.restart()
    assert seen == [kernel]
    assert get_current_kernel() is None


async def test_build_kernel_startup_invariants_pass(caplog):
    """构建内核时启动自检不产生「运行时不变量失败」告警。"""
    with caplog.at_level(logging.WARNING, logger="src.plugins.invariants"):
        kernel = await build_kernel("minimal", discover_external=False)
    try:
        failures = [
            record.getMessage()
            for record in caplog.records
            if "运行时不变量失败" in record.getMessage()
        ]
        assert failures == []
    finally:
        await shutdown_kernel(kernel)
