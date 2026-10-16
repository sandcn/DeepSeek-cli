"""Escape 监看插件测试 — 活跃实例内核服务接入点。

覆盖：
- 清单声明 ``escape_monitor`` 条目并注册内核服务；
- 服务提供活跃实例查询/停止/创建接入点；
- 模块级 ``get_active_monitor`` / ``stop_active_monitor`` 经内核服务解析；
- ``_registry`` 进程级单例 API 与历史行为一致。
"""

from __future__ import annotations

import pytest

from src.api.escape_monitor import (
    EscapeMonitor,
    get_active_monitor,
    stop_active_monitor,
)
from src.api.escape_monitor import _registry
from src.plugins.bootstrap import build_kernel, shutdown_kernel


class _FakeMonitor:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


@pytest.fixture(autouse=True)
def _clean_registry():
    _registry.clear_active_monitor()
    yield
    _registry.clear_active_monitor()


@pytest.fixture
async def cli_kernel():
    kernel = await build_kernel("cli")
    try:
        yield kernel
    finally:
        await shutdown_kernel(kernel)


async def test_service_registered(cli_kernel):
    service = cli_kernel.resolve_service("escape_monitor")
    assert service.active() is None
    monitor = _FakeMonitor()
    service.set_active(monitor)
    try:
        assert service.active() is monitor
    finally:
        service.clear_active()
    assert service.active() is None


async def test_module_api_uses_kernel_service(cli_kernel):
    service = cli_kernel.resolve_service("escape_monitor")
    monitor = _FakeMonitor()
    service.set_active(monitor)
    try:
        assert get_active_monitor() is monitor
        stop_active_monitor()
        assert monitor.stopped is True
    finally:
        service.clear_active()


async def test_service_create_cli_kernel(cli_kernel):
    service = cli_kernel.resolve_service("escape_monitor")
    monitor = service.create(object())
    assert isinstance(monitor, EscapeMonitor)


def test_registry_raw_api():
    monitor = _FakeMonitor()
    _registry.set_active_monitor(monitor)
    try:
        assert _registry.raw_active_monitor() is monitor
        _registry.raw_stop_monitor()
        assert monitor.stopped is True
    finally:
        _registry.clear_active_monitor()
    assert _registry.raw_active_monitor() is None


def test_module_api_without_kernel():
    monitor = _FakeMonitor()
    _registry.set_active_monitor(monitor)
    try:
        assert get_active_monitor() is monitor
        stop_active_monitor()
        assert monitor.stopped is True
    finally:
        _registry.clear_active_monitor()
