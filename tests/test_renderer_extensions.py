"""渲染器扩展点测试 — TokenHandler / TokenFilter 的插件化注册。

覆盖：
- extensions 注册 / 撤销；
- RenderEngine 显式 extra_handlers 与全局注册表两条路径；
- IncrementalRenderer / AnsiStreamRenderer 使用扩展过滤器；
- 插件 Fiber 卸载时撤销其注册的扩展。
"""

from __future__ import annotations

import io

import pytest

from src.renderer.extensions import (
    clear,
    filter_factories,
    handler_factories,
    register_filter,
    register_handler,
)
from src.renderer.handlers.base import TokenHandler
from src.renderer.types import TokenType
from src.plugins.bootstrap import build_kernel, shutdown_kernel


class _MarkerHandler(TokenHandler):
    def __init__(self):
        self.seen = []

    def get_token_types(self):
        return {TokenType.EMPTY_LINE}

    def handle(self, token, engine):
        self.seen.append(token)


class _MarkerFilter:
    def __init__(self):
        self.calls = 0

    def process(self, tokens, ctx):
        self.calls += 1
        return tokens


@pytest.fixture(autouse=True)
def clean_extensions():
    clear()
    yield
    clear()


@pytest.fixture
async def cli_kernel():
    kernel = await build_kernel("cli")
    try:
        yield kernel
    finally:
        await shutdown_kernel(kernel)


def _adapter():
    from rich.console import Console

    from src.renderer.output import OutputAdapter

    return OutputAdapter(Console(file=io.StringIO(), width=60))


def test_register_and_unregister_handler():
    undo = register_handler(lambda: _MarkerHandler())
    assert len(handler_factories()) == 1
    undo()
    assert handler_factories() == ()


def test_register_and_unregister_filter():
    undo = register_filter(lambda: _MarkerFilter())
    assert len(filter_factories()) == 1
    undo()
    assert filter_factories() == ()


def test_register_rejects_non_callable():
    with pytest.raises(TypeError):
        register_handler(object())
    with pytest.raises(TypeError):
        register_filter(object())


def test_engine_uses_explicit_extra_handler():
    from src.renderer.engine import RenderEngine

    engine = RenderEngine(_adapter(), extra_handlers=[lambda: _MarkerHandler()])
    handler = engine._handler_registry.get(TokenType.EMPTY_LINE)
    assert isinstance(handler, _MarkerHandler)


def test_engine_uses_registered_handler():
    from src.renderer.engine import RenderEngine

    register_handler(lambda: _MarkerHandler())
    engine = RenderEngine(_adapter())
    assert isinstance(engine._handler_registry.get(TokenType.EMPTY_LINE), _MarkerHandler)


def test_incremental_renderer_uses_registered_filter():
    from src.renderer import IncrementalRenderer

    marker = _MarkerFilter()
    register_filter(lambda: marker)
    renderer = IncrementalRenderer(
        show_indicator=False, output_adapter=_adapter(), captured_output=[]
    )
    assert any(f is marker for f in renderer._pipeline._filters)


def test_ansi_renderer_uses_registered_filter():
    from src.renderer.ansi import AnsiStreamRenderer

    marker = _MarkerFilter()
    register_filter(lambda: marker)
    renderer = AnsiStreamRenderer(width=40)
    assert any(f is marker for f in renderer._pipeline._filters)


async def test_renderer_plugin_registers_extensions(cli_kernel):
    renderer = cli_kernel.resolve_service("renderer")
    undo_handler = renderer.register_handler(lambda: _MarkerHandler())
    undo_filter = renderer.register_filter(lambda: _MarkerFilter())
    try:
        assert renderer.handlers()
        assert renderer.filters()
    finally:
        undo_handler()
        undo_filter()
    assert renderer.handlers() == []
    assert renderer.filters() == []


async def test_renderer_extensions_revoked_on_unload(cli_kernel):
    from src.kernel import plugin

    @plugin("ext_render", inject=["renderer"])
    def apply(ctx):
        undo_handler = ctx.renderer.register_handler(lambda: _MarkerHandler())
        undo_filter = ctx.renderer.register_filter(lambda: _MarkerFilter())
        ctx.effect(lambda: undo_handler)
        ctx.effect(lambda: undo_filter)

    fiber = cli_kernel.mount(apply)
    await cli_kernel.settle()
    assert len(handler_factories()) == 1
    assert len(filter_factories()) == 1
    await fiber.dispose()
    await cli_kernel.settle()
    assert handler_factories() == ()
    assert filter_factories() == ()
