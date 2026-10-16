"""事件消费者插件测试 — ctx.consumers。

覆盖：
- 内核提供 ``ctx.consumers``（cli），其输出消费者订阅内核显示总线；
- ``create_chat_ui`` 委托 ChatUIConsumer；
- ``setup_error_handler`` / ``teardown_error_handler``（幂等）；
- 消费者 Fiber 卸载时自动注销错误处理器；
- ``plugins/app.bootstrap`` 经 ``ctx.consumers`` 装配输出消费者。
"""

from __future__ import annotations

import pytest

from src.plugins.bootstrap import build_kernel, shutdown_kernel


@pytest.fixture
async def cli_kernel():
    kernel = await build_kernel("cli")
    try:
        yield kernel
        # 测试结束确保处理器已注销，避免污染 root logger
        from src.tui.consumer import teardown_chat_ui_error_handler

        teardown_chat_ui_error_handler()
    finally:
        await shutdown_kernel(kernel)


async def test_consumers_service_present(cli_kernel):
    from src.plugins.consumers import ConsumersService

    assert isinstance(cli_kernel.resolve_service("consumers"), ConsumersService)


async def test_minimal_profile_has_no_consumers():
    kernel = await build_kernel("minimal")
    try:
        assert not kernel.has_service("consumers")
    finally:
        await shutdown_kernel(kernel)


async def test_output_consumer_subscribes_kernel_display_bus(cli_kernel):
    service = cli_kernel.resolve_service("consumers")
    bus = cli_kernel.resolve_service("events").display_bus
    assert service.display_bus() is bus
    consumer = service.create_output_consumer(chat_ui_managed=False)
    assert consumer._bus is bus


async def test_create_chat_ui_delegates(cli_kernel, monkeypatch):
    import src.tui.consumer as consumer_mod

    sentinel = object()
    monkeypatch.setattr(consumer_mod, "ChatUIConsumer", lambda: sentinel)
    assert cli_kernel.resolve_service("consumers").create_chat_ui() is sentinel


async def test_error_handler_setup_and_teardown(cli_kernel):
    import src.tui.consumer as consumer_mod

    service = cli_kernel.resolve_service("consumers")
    consumer_mod.teardown_chat_ui_error_handler()
    assert consumer_mod._error_handler_registered is False

    service.setup_error_handler()
    assert consumer_mod._error_handler_registered is True
    service.setup_error_handler()
    assert consumer_mod._error_handler_registered is True

    service.teardown_error_handler()
    assert consumer_mod._error_handler_registered is False
    service.teardown_error_handler()
    assert consumer_mod._error_handler_registered is False


async def test_consumers_unload_teardown_error_handler():
    import src.tui.consumer as consumer_mod
    from src.kernel import Kernel
    from src.plugins.config import apply as config_apply
    from src.plugins.consumers import apply as consumers_apply
    from src.plugins.events import apply as events_apply

    consumer_mod.teardown_chat_ui_error_handler()
    kernel = Kernel(name="t")
    kernel.mount(config_apply)
    kernel.mount(events_apply)
    kernel.mount(consumers_apply)
    await kernel.settle()
    try:
        kernel.resolve_service("consumers").setup_error_handler()
        assert consumer_mod._error_handler_registered is True
    finally:
        await kernel.dispose()
    assert consumer_mod._error_handler_registered is False


async def test_app_bootstrap_uses_consumers_service(cli_kernel, monkeypatch):
    app = cli_kernel.resolve_service("app")
    service = cli_kernel.resolve_service("consumers")
    calls = []
    monkeypatch.setattr(service, "setup_error_handler", lambda: calls.append("setup"))
    monkeypatch.setattr(
        service, "create_output_consumer",
        lambda chat_ui_managed=True: calls.append(("consumer", chat_ui_managed)) or _FakeConsumer(),
    )
    monkeypatch.setattr(app, "_start_observability", lambda: calls.append("obs"))
    if app._bootstrapped:
        app._bootstrapped = False
    app.bootstrap()
    assert calls[0] == "setup"
    assert "obs" in calls
    assert ("consumer", True) in calls
    assert app._output_consumer is not None
    app.shutdown()


class _FakeConsumer:
    def __init__(self):
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False
