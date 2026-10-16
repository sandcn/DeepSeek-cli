"""渲染扩展独立插件条目测试 — 每个内置 handler/filter 一个清单条目。

覆盖：
- 清单为每个内置 handler/filter 声明独立条目（id 与内置 id 一一对应）；
- 默认 profile 经独立条目注册全部内置项（RenderEngine 装配到 handler）；
- overlay 禁用单个 handler/filter 真正生效（renderer_builtin 抑制默认装配）；
- 条目 config 的 handler 引用可替换实现（可用 patch/overlay 替换）；
- 直接 API：register/unregister/set_managed/disable 与 reset。
"""

from __future__ import annotations

import io

import pytest

from src.kernel import Kernel, materialize
from src.plugins.bootstrap import (
    build_kernel,
    inject_managed_config,
    resolve_entries,
    shutdown_kernel,
)

import src.renderer.extensions as ext
import src.core.middleware.registry as mw


@pytest.fixture(autouse=True)
def _clean_registries():
    ext.reset()
    mw.reset()
    yield
    ext.reset()
    mw.reset()


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


def _resolved(profile="cli"):
    return materialize(resolve_entries(profile, discover_external=False))


def test_manifest_declares_each_handler_and_filter():
    handler_ids = []
    filter_ids = []
    for entry, plug in _resolved():
        name = getattr(plug, "name", "")
        if name == "renderer_handler":
            handler_ids.append((entry.id, (entry.config or {}).get("id")))
        elif name == "renderer_filter":
            filter_ids.append((entry.id, (entry.config or {}).get("id")))
    assert [spec_id for _, spec_id in handler_ids] == ext.builtin_handler_ids()
    assert [spec_id for _, spec_id in filter_ids] == ext.builtin_filter_ids()
    assert len({entry_id for entry_id, _ in handler_ids}) == len(handler_ids)
    assert len({entry_id for entry_id, _ in filter_ids}) == len(filter_ids)
    assert all(i.startswith("renderer_ext::renderer_handler_") for i, _ in handler_ids)
    assert all(i.startswith("renderer_ext::renderer_filter_") for i, _ in filter_ids)


def test_tree_declares_renderer_ext_bundle():
    from src.plugins.manifest import build_config_tree

    tree = build_config_tree()
    assert "renderer_ext" in tree.bundles()
    assert "renderer_ext" in tree.bundle("presentation").includes


async def test_default_profile_registers_all_builtin(cli_kernel):
    assert len(ext.builtin_handler_factories()) == 11
    assert len(ext.builtin_filter_factories()) == 3

    from src.renderer.engine import RenderEngine
    from src.renderer.types import TokenType

    engine = RenderEngine(_adapter())
    assert engine._handler_registry.get(TokenType.HEADING) is not None
    assert engine._handler_registry.get(TokenType.CODE_FENCE_OPEN) is not None


async def test_renderer_service_introspection(cli_kernel):
    renderer = cli_kernel.resolve_service("renderer")
    assert len(renderer.builtin_handlers()) == 11
    assert len(renderer.builtin_filters()) == 3
    assert renderer.builtin_handler_ids() == ext.builtin_handler_ids()
    assert renderer.builtin_filter_ids() == ext.builtin_filter_ids()


async def _build_with_disable(ids):
    from src.kernel.overlay import apply_overlay

    entries = apply_overlay(resolve_entries("cli", discover_external=False), {"disable": list(ids)})
    resolved = materialize(entries)
    inject_managed_config(resolved)
    kernel = Kernel(name="t", profile="cli")
    for entry, plug in resolved:
        if entry.disabled:
            continue
        kernel.mount(plug, config=entry.config)
    await kernel.settle()
    return kernel


async def test_overlay_disable_single_renderer_items():
    kernel = await _build_with_disable([
        "renderer_ext::renderer_handler_code",
        "renderer_ext::renderer_filter_stream_optimizer",
    ])
    try:
        assert len(ext.builtin_handler_factories()) == 10
        assert len(ext.builtin_filter_factories()) == 2
        names = [type(f()).__name__ for f in ext.builtin_handler_factories()]
        assert "CodeHandler" not in names
        assert "InlineHandler" in names
    finally:
        await kernel.dispose()


async def test_renderer_handler_entry_replaces_implementation():
    from src.plugins.config import apply as config_apply
    from src.plugins.renderer import apply as renderer_apply
    from src.plugins.renderer_entries import apply_handler

    kernel = Kernel(name="t")
    kernel.mount(config_apply)
    kernel.mount(renderer_apply)
    await kernel.settle()
    kernel.mount(apply_handler, config={"id": "code", "handler": "src.renderer.handlers.InlineHandler"})
    await kernel.settle()
    try:
        names = [type(f()).__name__ for f in ext.builtin_handler_factories()]
        assert names.count("InlineHandler") == 2
        assert "CodeHandler" not in names
    finally:
        await kernel.dispose()
    names = [type(f()).__name__ for f in ext.builtin_handler_factories()]
    assert "CodeHandler" in names


async def test_renderer_filter_entry_replaces_implementation():
    from src.plugins.config import apply as config_apply
    from src.plugins.renderer import apply as renderer_apply
    from src.plugins.renderer_entries import apply_filter

    kernel = Kernel(name="t")
    kernel.mount(config_apply)
    kernel.mount(renderer_apply)
    await kernel.settle()
    kernel.mount(apply_filter, config={"id": "stream_optimizer", "filter": "src.renderer.pipeline.CodeBlockBatcher"})
    await kernel.settle()
    try:
        names = [type(f()).__name__ for f in ext.builtin_filter_factories()]
        assert names.count("CodeBlockBatcher") == 2
        assert "TokenStreamOptimizer" not in names
    finally:
        await kernel.dispose()
    names = [type(f()).__name__ for f in ext.builtin_filter_factories()]
    assert "TokenStreamOptimizer" in names


async def test_entry_without_id_fails():
    from src.plugins.config import apply as config_apply
    from src.plugins.renderer import apply as renderer_apply
    from src.plugins.renderer_entries import apply_handler

    kernel = Kernel(name="t")
    kernel.mount(config_apply)
    kernel.mount(renderer_apply)
    await kernel.settle()
    fiber = kernel.mount(apply_handler, config={})
    await kernel.settle()
    try:
        assert fiber.state.value == "FAILED"
        assert isinstance(fiber.error, ValueError)
    finally:
        await kernel.dispose()


def test_register_builtin_handler_api_and_unknown():
    undo = ext.register_builtin_handler("code")
    try:
        names = [type(f()).__name__ for f in ext.builtin_handler_factories()]
        assert names.count("CodeHandler") == 1
    finally:
        undo()
    with pytest.raises(KeyError):
        ext.register_builtin_handler("nope")


def test_set_managed_and_register_roundtrip():
    undo_manage = ext.set_managed_builtin_handlers(["code"])
    try:
        assert len(ext.builtin_handler_factories()) == 10
        undo_reg = ext.register_builtin_handler("code")
        try:
            assert len(ext.builtin_handler_factories()) == 11
        finally:
            undo_reg()
        assert len(ext.builtin_handler_factories()) == 10
    finally:
        undo_manage()
    assert len(ext.builtin_handler_factories()) == 11
    with pytest.raises(KeyError):
        ext.set_managed_builtin_handlers(["nope"])


def test_unregister_builtin_handler():
    assert ext.unregister_builtin_handler("inline") is False
    ext.register_builtin_handler("inline")
    assert ext.unregister_builtin_handler("inline") is True
