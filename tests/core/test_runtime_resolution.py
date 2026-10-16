"""运行时解析内核优先测试 — commands / skills / policy 经内核服务解析。

「一切皆插件」：这些子系统的全局访问函数（``default_registry`` /
``get_plugin_registry`` / ``get_excluded_tools`` / ``Func.can_use``）在有内核
时经内核服务解析，无内核时回退既有默认实现（单例/静态真源）。
"""

from __future__ import annotations

import pytest

from src.kernel import get_current_kernel
from src.plugins.bootstrap import build_kernel, shutdown_kernel


@pytest.fixture
async def cli_kernel():
    kernel = await build_kernel("cli")
    try:
        yield kernel
    finally:
        await shutdown_kernel(kernel)


async def test_skills_registry_resolves_via_kernel(cli_kernel):
    from src.skills.registry import default_registry

    assert default_registry() is cli_kernel.resolve_service("skills").registry


async def test_command_registry_resolves_via_kernel(cli_kernel):
    from src.core.commands.base import get_plugin_registry

    assert get_plugin_registry() is cli_kernel.resolve_service("commands").registry


async def test_excluded_tools_resolves_via_kernel(cli_kernel):
    from src.tools.tool_policy import get_excluded_tools

    assert "bash" in get_excluded_tools("review")
    assert "bash" not in get_excluded_tools("execute")


async def test_func_can_use_uses_policy_service(cli_kernel):
    from src.core.adapters.kernel_runtime import active_policy
    from src.tools.base import Func

    policy = active_policy()
    assert policy is not None
    allowed, reason = Func.can_use("bash", "review")
    assert allowed is False
    assert reason


async def test_func_can_use_plan_path_whitelist(cli_kernel):
    from src.tools.base import Func

    allowed, reason = Func.can_use("write_file", "plan", path="src/x.py")
    assert allowed is False
    allowed_ok, _ = Func.can_use("write_file", "plan", path=".chat/plan/a.md")
    assert allowed_ok is True


def test_skills_registry_fallback_without_kernel():
    from src.skills.registry import default_registry

    assert get_current_kernel() is None
    registry = default_registry()
    assert registry is not None


def test_command_registry_fallback_without_kernel():
    from src.core.commands.base import get_plugin_registry

    assert get_current_kernel() is None
    assert get_plugin_registry().count() >= 0


def test_excluded_tools_fallback_without_kernel():
    from src.tools.tool_policy import get_excluded_tools

    assert get_current_kernel() is None
    assert "bash" in get_excluded_tools("review")
