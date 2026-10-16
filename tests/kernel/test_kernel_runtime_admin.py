"""内核运行时管理测试 — 启用/禁用/重载、依赖诊断、文件热重载、ctx.kernel_admin。

覆盖：
- Fiber 运行时 disable/enable（可逆；禁用后服务消失，启用后恢复）；
- Kernel 的 is_enabled / enabled_plugins / disabled_plugins；
- 依赖诊断（dependency_report / why_blocked / service_providers / kernel_stats / diagnose）；
- 插件文件热重载（watch_file + reload_file / watcher.flush）；
- ctx.kernel_admin 服务自省与操作。
"""

from __future__ import annotations

import os
import textwrap

import pytest

from src.kernel import Kernel, plugin
from src.kernel.fiber import FiberState


@plugin("svc", provide=["svc"])
def apply_svc(ctx):
    return {"value": 1}


@plugin("dep", inject=["nope"])
def apply_dep(ctx):
    return {"value": 2}


async def test_disable_and_enable_fiber():
    kernel = Kernel(name="t")
    kernel.mount(apply_svc)
    await kernel.settle()
    try:
        assert kernel.has_service("svc") is True
        assert kernel.is_enabled("svc") is True

        await kernel.disable("svc")
        assert kernel.has_service("svc") is False
        assert kernel.is_enabled("svc") is False
        assert "svc" in kernel.disabled_plugins()
        assert kernel.fiber("svc").state is FiberState.DISPOSED

        await kernel.enable("svc")
        assert kernel.has_service("svc") is True
        assert kernel.is_enabled("svc") is True
        assert kernel.disabled_plugins() == []

        await kernel.set_enabled("svc", False)
        assert kernel.has_service("svc") is False
    finally:
        await kernel.dispose()


async def test_disable_missing_plugin_raises():
    from src.kernel.errors import PluginError

    kernel = Kernel(name="t")
    try:
        with pytest.raises(PluginError):
            await kernel.disable("nope")
        with pytest.raises(PluginError):
            await kernel.enable("nope")
    finally:
        await kernel.dispose()


async def test_dependency_diagnostics():
    kernel = Kernel(name="t", profile="cli")
    kernel.mount(apply_svc)
    kernel.mount(apply_dep)
    await kernel.settle()
    try:
        report = {item["name"]: item for item in kernel.dependency_report()}
        assert report["svc"]["state"] == "ACTIVE"
        assert report["dep"]["state"] == "PENDING"
        assert report["dep"]["missing"] == ["nope"]

        reasons = kernel.why_blocked("dep")
        assert any("nope" in r for r in reasons)

        assert kernel.service_providers("svc") == ["svc"]
        stats = kernel.kernel_stats()
        assert stats["fibers"] == 2
        assert stats["profile"] == "cli"
        assert "plugins:" in kernel.diagnose()
    finally:
        await kernel.dispose()


async def test_watch_file_hot_reload(tmp_path):
    path = tmp_path / "hot_plugin.py"
    path.write_text(textwrap.dedent(
        """
        from src.kernel import plugin

        @plugin("hot", provide=["hot"])
        def apply(ctx):
            return {"v": "v1"}
        """
    ), encoding="utf-8")

    kernel = Kernel(name="t")
    stop = kernel.watch_file(str(path))
    try:
        await kernel.reload_file(str(path))
        assert kernel.has_service("hot") is True
        assert kernel.resolve_service("hot")["v"] == "v1"

        # 修改文件内容 → flush 检测 mtime 并重载
        path.write_text(textwrap.dedent(
            """
            from src.kernel import plugin

            @plugin("hot", provide=["hot"])
            def apply(ctx):
                return {"v": "v2"}
            """
        ), encoding="utf-8")
        os.utime(str(path), None)
        affected = await kernel.watcher().flush()
        assert "hot" in affected
        assert kernel.resolve_service("hot")["v"] == "v2"
    finally:
        stop()
        await kernel.dispose()


async def test_watch_file_disposer():
    kernel = Kernel(name="t")
    try:
        stop = kernel.watch_file("some/nonexistent_plugin.py")
        assert kernel.watcher().watched()
        stop()
        assert kernel.watcher().watched() == []
    finally:
        await kernel.dispose()


async def test_kernel_admin_service():
    from src.plugins.bootstrap import build_kernel, shutdown_kernel

    kernel = await build_kernel("cli")
    try:
        admin = kernel.resolve_service("kernel_admin")
        assert "tools" in admin.enabled()
        stats = admin.stats()
        assert stats["fibers"] > 0
        assert isinstance(admin.plugins(), list)
        assert "plugins:" in admin.diagnose()

        state = await admin.disable("presets")
        assert state == "DISPOSED"
        assert admin.is_enabled("presets") is False
        state = await admin.enable("presets")
        assert state == "ACTIVE"
        assert admin.is_enabled("presets") is True
    finally:
        await shutdown_kernel(kernel)
