"""通知插件测试 — ctx.notifications 服务与 provider 替换。

覆盖：
- 默认 provider 转发 src.notifications；
- set_provider 替换与转发（同步 / 异步回退）；
- 应用层经服务发送通知（内核优先）；
- 无内核回退直接调用。
"""

from __future__ import annotations

import pytest

from src.kernel import set_current_kernel
from src.plugins.bootstrap import build_kernel, shutdown_kernel


@pytest.fixture
async def cli_kernel():
    kernel = await build_kernel("cli")
    try:
        yield kernel
    finally:
        await shutdown_kernel(kernel)


class _FakeNotifier:
    def __init__(self):
        self.calls = []

    def notify_chat_completed(self, messages, elapsed=None):
        self.calls.append((messages, elapsed))


async def test_service_forwards_to_default_provider(cli_kernel, monkeypatch):
    import src.notifications as notif

    seen = []
    monkeypatch.setattr(
        notif,
        "notify_chat_completed",
        lambda messages, elapsed=None: seen.append((messages, elapsed)),
    )
    service = cli_kernel.resolve_service("notifications")
    service.notify_chat_completed([{"role": "user"}], elapsed=1.5)
    assert seen == [([{"role": "user"}], 1.5)]


async def test_set_provider_and_forward(cli_kernel):
    service = cli_kernel.resolve_service("notifications")
    fake = _FakeNotifier()
    previous = service.set_provider(fake)
    try:
        service.notify_chat_completed([{"role": "user"}], elapsed=2.0)
        assert fake.calls == [([{"role": "user"}], 2.0)]
        assert service.port() is fake
    finally:
        service.set_provider(previous)


async def test_async_notify_falls_back_to_sync(cli_kernel):
    service = cli_kernel.resolve_service("notifications")
    fake = _FakeNotifier()
    previous = service.set_provider(fake)
    try:
        await service.async_notify_chat_completed([{"role": "user"}], elapsed=1.0)
        assert fake.calls == [([{"role": "user"}], 1.0)]
    finally:
        service.set_provider(previous)


async def test_session_setup_uses_kernel_service(cli_kernel):
    from src.app_loop._session_setup import _notify_chat_completed

    service = cli_kernel.resolve_service("notifications")
    fake = _FakeNotifier()
    previous = service.set_provider(fake)
    try:
        _notify_chat_completed([{"role": "user"}], 3.0)
        assert fake.calls == [([{"role": "user"}], 3.0)]
    finally:
        service.set_provider(previous)


def test_session_setup_fallback_without_kernel(monkeypatch):
    set_current_kernel(None)
    import src.notifications as notif

    seen = []
    monkeypatch.setattr(
        notif,
        "notify_chat_completed",
        lambda messages, elapsed=None: seen.append((messages, elapsed)),
    )
    from src.app_loop._session_setup import _notify_chat_completed

    _notify_chat_completed([{"role": "user"}], 4.0)
    assert seen == [([{"role": "user"}], 4.0)]
