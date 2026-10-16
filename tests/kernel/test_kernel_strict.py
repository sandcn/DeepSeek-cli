"""内核严格模式测试 — 生产路径必需服务缺失显式失败。

覆盖：
- 组合根按 Profile 声明激活严格模式（必需服务集合 = 插件树 provide 声明）；
- 必需服务缺失抛 ``ServiceUnavailable``（不再静默回退）；
- 非必需服务缺失仍返回默认值（回退语义保留）；
- 无内核 / 内核卸载后严格模式关闭，回退语义恢复；
- 与严格内核不同的内核不触发严格检查。
"""

from __future__ import annotations

import pytest

import src.kernel.runtime as kr
from src.kernel.runtime import (
    ServiceUnavailable,
    active_service,
    deactivate_strict,
    require_service,
    strict_enabled,
    strict_required,
)
from src.plugins.bootstrap import build_kernel, declared_provide_keys, shutdown_kernel


@pytest.fixture(autouse=True)
def _clean_strict():
    deactivate_strict()
    yield
    deactivate_strict()


@pytest.fixture
async def cli_kernel():
    kernel = await build_kernel("cli")
    try:
        yield kernel
    finally:
        await shutdown_kernel(kernel)


def test_declared_provide_keys_from_manifest():
    from src.kernel import materialize
    from src.plugins.bootstrap import resolve_entries

    resolved = materialize(resolve_entries("cli", discover_external=False))
    keys = declared_provide_keys(resolved)
    for expected in ("tools", "llm", "sessions", "agent_loop", "ui", "renderer"):
        assert expected in keys


async def test_strict_activated_with_declared_keys(cli_kernel):
    assert strict_enabled() is True
    assert "tools" in strict_required()
    assert "ui" in strict_required()
    assert active_service("tools") is not None


async def test_required_missing_raises(cli_kernel):
    # 删除一个必需服务 → 解析即显式失败
    saved = cli_kernel._services.pop("tools", None)
    try:
        with pytest.raises(ServiceUnavailable) as exc:
            active_service("tools")
        assert "tools" in str(exc.value)
        with pytest.raises(ServiceUnavailable):
            require_service("tools")
    finally:
        if saved is not None:
            cli_kernel._services["tools"] = saved


async def test_non_required_missing_returns_default(cli_kernel):
    # 非声明服务缺失 → 回退默认（不抛错）
    assert active_service("not_a_service") is None
    assert active_service("not_a_service", "fallback") == "fallback"
    assert require_service("not_a_service") is None


async def test_minimal_profile_not_require_ui():
    kernel = await build_kernel("minimal")
    try:
        assert strict_enabled() is True
        assert "ui" not in strict_required()
        assert active_service("ui", "none") == "none"
    finally:
        await shutdown_kernel(kernel)


async def test_shutdown_deactivates_strict(cli_kernel):
    await shutdown_kernel(cli_kernel)
    assert strict_enabled() is False
    assert strict_required() == frozenset()


def test_no_kernel_no_strict():
    from src.kernel import set_current_kernel

    set_current_kernel(None)
    kr.activate_strict(["tools"])
    try:
        assert active_service("tools", "d") == "d"
    finally:
        deactivate_strict()


async def test_strict_only_applies_to_activation_kernel(cli_kernel):
    from src.kernel import Kernel

    other = Kernel(name="other", profile="cli")
    await other.settle()
    try:
        # 其它内核不触发严格检查（严格模式绑定激活内核）
        assert active_service("does-not-exist", "d") == "d"
    finally:
        await other.dispose()
