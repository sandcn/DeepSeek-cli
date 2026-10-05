"""entry-points 插件分发测试 — build_kernel 自动发现并挂载 ``dsh.plugins`` 组。

覆盖：
- 组合根挂载 entry-point 声明的插件；
- 单个 entry-point 加载失败被隔离，不阻断内核构建。
"""

from __future__ import annotations

import pytest

import src.plugins.bootstrap as bootstrap
from src.kernel import plugin
from src.plugins.bootstrap import build_kernel, shutdown_kernel


@plugin("ep_demo")
def _ep_demo(ctx):
    ctx.provide("ep_demo", {"ok": True})


@plugin("ep_bad")
def _ep_bad(ctx):
    raise RuntimeError("boom")


async def test_entry_point_plugin_is_mounted(monkeypatch):
    monkeypatch.setattr(
        bootstrap, "entry_points_plugins",
        lambda group="dsh.plugins": [("ep_demo", _ep_demo)],
    )
    kernel = await build_kernel("minimal")
    try:
        assert kernel.fiber("ep_demo") is not None
        assert kernel.has_service("ep_demo")
    finally:
        await shutdown_kernel(kernel)


async def test_entry_point_failure_isolated(monkeypatch):
    monkeypatch.setattr(
        bootstrap, "entry_points_plugins",
        lambda group="dsh.plugins": [("ep_bad", _ep_bad)],
    )
    kernel = await build_kernel("minimal")
    try:
        fiber = kernel.fiber("ep_bad")
        assert fiber is not None
        assert fiber.state.value == "FAILED"
        # 内核其余部分照常启动
        assert kernel.has_service("config")
    finally:
        await shutdown_kernel(kernel)


async def test_entry_points_not_called_when_discovery_disabled(monkeypatch):
    called = {"n": 0}

    def _spy(group="dsh.plugins"):
        called["n"] += 1
        return []

    monkeypatch.setattr(bootstrap, "entry_points_plugins", _spy)
    kernel = await build_kernel("minimal", discover_external=False)
    try:
        assert called["n"] == 0
    finally:
        await shutdown_kernel(kernel)
