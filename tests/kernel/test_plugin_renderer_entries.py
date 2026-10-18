"""渲染扩展独立插件条目测试 — 每个内置 filter 一个清单条目。

覆盖：
- 清单为每个内置 filter 声明独立条目（id 与内置 id 一一对应）；
- 默认 profile 经独立条目注册全部内置项（AnsiStreamRenderer 装配到 filter）；
- overlay 禁用单个 filter 真正生效（renderer_builtin 抑制默认装配）；
- 条目 config 的 filter 引用可替换实现（可用 patch/overlay 替换）；
- 直接 API：register/unregister/set_managed/disable 与 reset。
"""

from __future__ import annotations

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


def _resolved(profile="cli"):
    return materialize(resolve_entries(profile, discover_external=False))


def test_manifest_declares_each_filter():
    filter_ids = []
    for entry, plug in _resolved():
        name = getattr(plug, "name", "")
        if name == "renderer_filter":
            filter_ids.append((entry.id, (entry.config or {}).get("id")))
    assert [spec_id for _, spec_id in filter_ids] == ext.builtin_filter_ids()
    assert len({entry_id for entry_id, _ in filter_ids}) == len(filter_ids)
    assert all(i.startswith("renderer_ext::renderer_filter_") for i, _ in filter_ids)


def test_tree_declares_renderer_ext_bundle():
    from src.plugins.manifest import build_config_tree

    tree = build_config_tree()
    assert "renderer_ext" in tree.bundles()
    assert "renderer_ext" in tree.bundle("presentation").includes


async def test_default_profile_registers_all_builtin(cli_kernel):
    assert len(ext.builtin_filter_factories()) == 3

    from src.renderer.ansi import AnsiStreamRenderer
    from src.renderer.pipeline_filters import TokenStreamOptimizer

    renderer = AnsiStreamRenderer(width=60)
    kinds = [type(f) for f in renderer._pipeline._filters]
    assert TokenStreamOptimizer in kinds
    renderer.close()


async def test_renderer_service_introspection(cli_kernel):
    renderer = cli_kernel.resolve_service("renderer")
    assert len(renderer.builtin_filters()) == 3
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


async def test_overlay_disable_single_renderer_filter():
    kernel = await _build_with_disable([
        "renderer_ext::renderer_filter_stream_optimizer",
    ])
    try:
        assert len(ext.builtin_filter_factories()) == 2
        names = [type(f()).__name__ for f in ext.builtin_filter_factories()]
        assert "TokenStreamOptimizer" not in names
        assert "CodeBlockBatcher" in names
    finally:
        await kernel.dispose()


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
    from src.plugins.renderer_entries import apply_filter

    kernel = Kernel(name="t")
    kernel.mount(config_apply)
    kernel.mount(renderer_apply)
    await kernel.settle()
    fiber = kernel.mount(apply_filter, config={})
    await kernel.settle()
    try:
        assert fiber.state.value == "FAILED"
        assert isinstance(fiber.error, ValueError)
    finally:
        await kernel.dispose()


def test_register_builtin_filter_api_and_unknown():
    undo = ext.register_builtin_filter("stream_optimizer")
    try:
        names = [type(f()).__name__ for f in ext.builtin_filter_factories()]
        assert names.count("TokenStreamOptimizer") == 1
    finally:
        undo()
    with pytest.raises(KeyError):
        ext.register_builtin_filter("nope")


def test_set_managed_and_register_roundtrip():
    undo_manage = ext.set_managed_builtin_filters(["stream_optimizer"])
    try:
        assert len(ext.builtin_filter_factories()) == 2
        undo_reg = ext.register_builtin_filter("stream_optimizer")
        try:
            assert len(ext.builtin_filter_factories()) == 3
        finally:
            undo_reg()
        assert len(ext.builtin_filter_factories()) == 2
    finally:
        undo_manage()
    assert len(ext.builtin_filter_factories()) == 3
    with pytest.raises(KeyError):
        ext.set_managed_builtin_filters(["nope"])


def test_unregister_builtin_filter():
    assert ext.unregister_builtin_filter("heading_anchor") is False
    ext.register_builtin_filter("heading_anchor")
    assert ext.unregister_builtin_filter("heading_anchor") is True
