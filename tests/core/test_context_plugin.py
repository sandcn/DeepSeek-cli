"""上下文插件测试 — ctx.context 服务与压缩策略注册。

覆盖：
- 默认策略链（summarize → drop）；
- register_strategy / unregister / set_strategy_builder；
- manager 创建 ContextManager（config_port 默认注入）；
- ChatSession.initialize 经内核服务创建 ContextManager。
"""

from __future__ import annotations

import pytest

from src.plugins.bootstrap import build_kernel, shutdown_kernel


@pytest.fixture
async def cli_kernel():
    kernel = await build_kernel("cli")
    try:
        yield kernel
    finally:
        await shutdown_kernel(kernel)


async def test_context_service_manager(cli_kernel):
    from src.core.context_manager import ContextManager

    service = cli_kernel.resolve_service("context")
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
    manager = service.manager(messages=messages, model="deepseek-chat")
    try:
        assert isinstance(manager, ContextManager)
        assert manager.model == "deepseek-chat"
    finally:
        manager.shutdown()


async def test_default_strategy_chain(cli_kernel):
    from src.core.compression import DropStrategy, SummarizeStrategy

    service = cli_kernel.resolve_service("context")
    names = service.strategy_names()
    assert "summarize" in names
    assert "drop" in names
    built = service.build_strategies()
    assert isinstance(built[0], SummarizeStrategy)
    assert isinstance(built[1], DropStrategy)


async def test_register_strategy_and_undo(cli_kernel):
    from src.core.compression import CompressionResult, CompressionStrategy

    service = cli_kernel.resolve_service("context")

    class MyStrategy(CompressionStrategy):
        def compress(self, messages, model, summarize_fn, on_changed, cache, force, on_info=None):
            return CompressionResult(success=False)

    undo = service.register_strategy("mine", lambda: MyStrategy())
    try:
        assert "mine" in service.strategy_names()
        assert isinstance(service.build_strategies(("mine",))[0], MyStrategy)
        assert isinstance(service.get_strategy("mine")(), MyStrategy)
    finally:
        undo()
    assert "mine" not in service.strategy_names()


async def test_unknown_strategy_raises(cli_kernel):
    service = cli_kernel.resolve_service("context")
    with pytest.raises(KeyError):
        service.get_strategy("nope")


async def test_register_strategy_validates(cli_kernel):
    service = cli_kernel.resolve_service("context")
    with pytest.raises(ValueError):
        service.register_strategy("", lambda: None)
    with pytest.raises(TypeError):
        service.register_strategy("bad", object())


async def test_strategy_builder_override(cli_kernel):
    from src.core.compression import DropStrategy

    service = cli_kernel.resolve_service("context")
    previous = service.set_strategy_builder(lambda names: [DropStrategy()])
    try:
        built = service.build_strategies()
        assert len(built) == 1
        assert isinstance(built[0], DropStrategy)
    finally:
        service.set_strategy_builder(previous)
    assert len(service.build_strategies()) == 2


async def test_session_initialize_uses_context_service(cli_kernel, monkeypatch):
    from src.core.session import ChatSession

    service = cli_kernel.resolve_service("context")
    calls = []
    original = service.manager

    def spy(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(service, "manager", spy)
    session = ChatSession()
    session.initialize()
    assert calls
    assert session.context_manager is not None


def test_context_service_without_kernel_via_root_context():
    from src.core.context_manager import ContextManager
    from src.kernel import Kernel
    from src.plugins.context import ContextService

    class _StubConfig:
        port = object()

        def model(self):
            return "stub-model"

    kernel = Kernel()
    kernel.provide("config", _StubConfig())
    service = ContextService(kernel.root)
    manager = service.manager(messages=[{"role": "user", "content": "hi"}])
    try:
        assert isinstance(manager, ContextManager)
        assert manager.model == "stub-model"
    finally:
        manager.shutdown()
