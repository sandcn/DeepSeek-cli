"""渲染器扩展点测试 — TokenFilter 的插件化注册。

覆盖：
- extensions 注册 / 撤销；
- AnsiStreamRenderer 使用扩展过滤器；
- 插件 Fiber 卸载时撤销其注册的扩展。
"""

from __future__ import annotations

import pytest

from src.renderer.extensions import (
    clear,
    filter_factories,
    register_filter,
)
from src.plugins.bootstrap import build_kernel, shutdown_kernel


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


def test_register_and_unregister_filter():
    undo = register_filter(lambda: _MarkerFilter())
    assert len(filter_factories()) == 1
    undo()
    assert filter_factories() == ()


def test_register_rejects_non_callable():
    with pytest.raises(TypeError):
        register_filter(object())


def test_ansi_renderer_uses_registered_filter():
    from src.renderer.ansi import AnsiStreamRenderer

    marker = _MarkerFilter()
    register_filter(lambda: marker)
    renderer = AnsiStreamRenderer(width=40)
    assert any(f is marker for f in renderer._pipeline._filters)


async def test_renderer_plugin_registers_extensions(cli_kernel):
    renderer = cli_kernel.resolve_service("renderer")
    undo_filter = renderer.register_filter(lambda: _MarkerFilter())
    try:
        assert renderer.filters()
    finally:
        undo_filter()
    assert renderer.filters() == []


async def test_renderer_extensions_revoked_on_unload(cli_kernel):
    from src.kernel import plugin

    @plugin("ext_render", inject=["renderer"])
    def apply(ctx):
        undo_filter = ctx.renderer.register_filter(lambda: _MarkerFilter())
        ctx.effect(lambda: undo_filter)

    fiber = cli_kernel.mount(apply)
    await cli_kernel.settle()
    assert len(filter_factories()) == 1
    await fiber.dispose()
    await cli_kernel.settle()
    assert filter_factories() == ()
