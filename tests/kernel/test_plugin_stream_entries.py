"""流式处理器独立插件条目测试 — 每个内置流式 chunk 处理器一个清单条目。

覆盖：
- 清单为每个内置处理器声明独立条目（id 一一对应）；
- 默认 profile 经独立条目注册全部处理器；
- overlay 禁用单个处理器真正生效（``stream`` 抑制默认装配）；
- 条目 config 的 handler 引用可替换实现；
- ``AsyncStreamPipeline`` 经注册表解析处理器（覆盖/禁用生效）；
- 直接 API：register/set_managed/disable/register_stream_handler 与 reset。
"""

from __future__ import annotations

import pytest

import src.api.stream.registry as sreg
from src.kernel import Kernel, materialize
from src.plugins.bootstrap import (
    build_kernel,
    inject_managed_config,
    resolve_entries,
    shutdown_kernel,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    sreg.reset()
    yield
    sreg.reset()


@pytest.fixture
async def cli_kernel():
    kernel = await build_kernel("cli")
    try:
        yield kernel
    finally:
        await shutdown_kernel(kernel)


def _resolved(profile="cli"):
    return materialize(resolve_entries(profile, discover_external=False))


def test_manifest_declares_each_stream_handler():
    specs = [entry.config.get("id") for entry, plug in _resolved() if getattr(plug, "name", "") == "stream_handler"]
    assert specs == sreg.builtin_stream_handler_ids()
    ids = [entry.id for entry, plug in _resolved() if getattr(plug, "name", "") == "stream_handler"]
    assert all(i.startswith("stream::stream_handler_") for i in ids)
    assert len(ids) == len(set(ids))


def test_tree_declares_stream_bundle():
    from src.plugins.manifest import build_config_tree

    tree = build_config_tree()
    assert "stream" in tree.bundles()
    assert "stream" in tree.bundle("runtime").includes


async def test_default_profile_registers_all_builtin(cli_kernel):
    service = cli_kernel.resolve_service("stream")
    assert set(service.handlers()) == {"reasoning", "content", "tool_calls", "speed"}
    pipeline = service.create_pipeline()
    assert all(
        getattr(pipeline, attr) is not None
        for attr in ("_reasoning_handler", "_content_handler", "_tool_calls_handler", "_speed_handler")
    )


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


async def test_overlay_disable_single_stream_handler():
    kernel = await _build_with_disable(["stream::stream_handler_speed"])
    try:
        service = kernel.resolve_service("stream")
        assert "speed" not in service.handlers()
        assert "content" in service.handlers()
        assert sreg.resolve_stream_handler("speed") is None
        # 缺席角色回退空处理器：保留核心累积、禁用副作用，管线不中断
        from src.api.stream.handlers.speed import SpeedHandler

        null = sreg.resolved_stream_handler("speed")
        assert isinstance(null, SpeedHandler)
        assert null.final_update(None) is None
    finally:
        await kernel.dispose()
    assert "speed" in sreg.builtin_stream_handler_factories()


async def test_pipeline_uses_registry_override():
    from src.api.stream.pipeline_async import AsyncStreamPipeline

    marker = object()

    class _FakeContent:
        def __init__(self):
            self.marker = marker

        def handle(self, ctx, text, token_est=None):
            return None

        def flush(self, label=None):
            return None

    undo = sreg.register_stream_handler("content", _FakeContent)
    try:
        pipeline = AsyncStreamPipeline()
        assert pipeline._content_handler.marker is marker
        # 真实内容处理器（内置）仍用于其他角色
        from src.api.stream.handlers.reasoning import ReasoningHandler

        assert isinstance(pipeline._reasoning_handler, ReasoningHandler)
    finally:
        undo()
    assert not isinstance(AsyncStreamPipeline()._content_handler, _FakeContent)


async def test_entry_config_replaces_implementation():
    from src.api.stream.pipeline_async import AsyncStreamPipeline
    from src.plugins.config import apply as config_apply
    from src.plugins.stream_entries import apply_stream_handler

    kernel = Kernel(name="t")
    kernel.mount(config_apply)
    await kernel.settle()
    kernel.mount(
        apply_stream_handler,
        config={"id": "speed", "handler": "src.api.stream.handlers.content.ContentHandler"},
    )
    await kernel.settle()
    try:
        pipeline = AsyncStreamPipeline()
        from src.api.stream.handlers.content import ContentHandler

        assert isinstance(pipeline._speed_handler, ContentHandler)
    finally:
        await kernel.dispose()
    from src.api.stream.handlers.speed import SpeedHandler

    assert isinstance(AsyncStreamPipeline()._speed_handler, SpeedHandler)


async def test_entry_without_id_fails():
    from src.plugins.config import apply as config_apply
    from src.plugins.stream_entries import apply_stream_handler

    kernel = Kernel(name="t")
    kernel.mount(config_apply)
    await kernel.settle()
    fiber = kernel.mount(apply_stream_handler, config={})
    await kernel.settle()
    try:
        assert fiber.state.value == "FAILED"
        assert isinstance(fiber.error, ValueError)
    finally:
        await kernel.dispose()


def test_registry_direct_api():
    assert len(sreg.builtin_stream_handler_factories()) == 4
    undo = sreg.disable_builtin_stream_handlers(["tool_calls"])
    try:
        assert "tool_calls" not in sreg.builtin_stream_handler_factories()
        assert sreg.resolve_stream_handler("tool_calls") is None
    finally:
        undo()
    assert "tool_calls" in sreg.builtin_stream_handler_factories()

    undo_manage = sreg.set_managed_builtin_stream_handlers(["content"])
    try:
        assert "content" not in sreg.builtin_stream_handler_factories()
    finally:
        undo_manage()
    assert "content" in sreg.builtin_stream_handler_factories()

    with pytest.raises(KeyError):
        sreg.disable_builtin_stream_handlers(["nope"])


def test_register_stream_handler_roundtrip():
    factory = lambda: None  # noqa: E731
    undo = sreg.register_stream_handler("reasoning", factory)
    try:
        assert sreg.stream_handler_factories()["reasoning"] is factory
    finally:
        undo()
    assert "reasoning" not in sreg.stream_handler_factories()
