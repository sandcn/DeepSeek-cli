"""进程级单例收敛为内核服务测试。

覆盖：
- ``ToolRegistry.default()`` / ``ToolScheduler.default()`` /
  ``LlmProviderRegistry.default_registry()`` / ``SkillRegistry.default_registry()``
  在内核挂载后返回内核服务持有的实例；
- 内核缺失时稳定回退进程级单例。
"""

from __future__ import annotations

import pytest

from src.plugins.bootstrap import build_kernel, shutdown_kernel


@pytest.fixture
async def minimal_kernel():
    kernel = await build_kernel("minimal")
    try:
        yield kernel
    finally:
        await shutdown_kernel(kernel)


@pytest.fixture
async def headless_kernel():
    kernel = await build_kernel("headless")
    try:
        yield kernel
    finally:
        await shutdown_kernel(kernel)


async def test_tool_registry_default_is_kernel_service(minimal_kernel):
    from src.tools.registry import ToolRegistry

    assert ToolRegistry.default() is minimal_kernel.resolve_service("tools").registry


async def test_llm_provider_registry_is_kernel_service(minimal_kernel):
    from src.api.provider_registry import default_registry

    assert default_registry() is minimal_kernel.resolve_service("llm").provider_registry()


async def test_skill_registry_is_kernel_service(minimal_kernel):
    from src.skills.registry import default_registry

    assert default_registry() is minimal_kernel.resolve_service("skills").registry


async def test_tool_scheduler_is_kernel_service(headless_kernel):
    from src.core.tool_executor_async import ToolScheduler

    service = headless_kernel.resolve_service("tool_scheduler")
    assert ToolScheduler.default() is service.scheduler


def test_singletons_fall_back_without_kernel():
    from src.kernel import set_current_kernel
    from src.tools.registry import ToolRegistry
    from src.core.tool_executor_async import ToolScheduler
    from src.api.provider_registry import default_registry

    set_current_kernel(None)
    assert ToolRegistry.default() is ToolRegistry.default()
    assert ToolScheduler.default() is ToolScheduler.default()
    assert default_registry() is default_registry()
