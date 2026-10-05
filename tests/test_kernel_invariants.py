"""运行时不变量测试 — ctx.invariants 自检插件树关系。"""

from __future__ import annotations

import pytest

from src.kernel import Kernel, plugin
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
