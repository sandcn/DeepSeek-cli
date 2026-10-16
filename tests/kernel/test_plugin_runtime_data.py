"""未接缝能力下沉为内核服务测试 — ctx.message_queue / multimodal / 选择 / 摘要 / 统计 / tokens。

覆盖：
- 默认 profile 经独立条目提供全部运行时数据服务；
- 服务委托底层模块（行为等价）；
- 内核优先接入点（_loop 消息队列 / model_async 图片瘦身）；
- overlay 禁用单个服务条目真正生效；
- 无内核回退直接构造/调用。
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


@pytest.fixture
async def cli_kernel():
    kernel = await build_kernel("cli")
    try:
        yield kernel
    finally:
        await shutdown_kernel(kernel)


def _resolved(profile="cli"):
    return materialize(resolve_entries(profile, discover_external=False))


def test_manifest_declares_runtime_data_entries():
    names = {getattr(p, "name", "") for _e, p in _resolved()}
    for key in ("message_queue", "multimodal", "context_selector",
                "context_summarizer", "stats", "tokens"):
        assert key in names
    from src.plugins.manifest import build_config_tree

    tree = build_config_tree()
    assert "runtime_data" in tree.bundle("core").includes


async def test_services_present(cli_kernel):
    for key in ("message_queue", "multimodal", "context_selector",
                "context_summarizer", "stats", "tokens"):
        assert cli_kernel.has_service(key)


async def test_message_queue_service(cli_kernel):
    service = cli_kernel.resolve_service("message_queue")
    queue = service.create()
    from src.core.message_queue import AsyncMessageQueue

    assert isinstance(queue, AsyncMessageQueue)
    msg = await queue.put("hello")
    got = await queue.get(timeout=0)
    assert got.content == "hello"
    assert service.default() is service.default()


async def test_multimodal_service(cli_kernel):
    service = cli_kernel.resolve_service("multimodal")
    assert service.is_multimodal("deepseek-flash") is True
    assert service.is_multimodal("deepseek-v4-pro") is False
    stats = service.optimize_messages_for_upload([{"role": "user", "content": "hi"}])
    assert isinstance(stats, dict)


async def test_context_selector_and_summarizer(cli_kernel):
    selector = cli_kernel.resolve_service("context_selector")
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}]
    assert selector.total_chars(messages) > 0
    assert selector.message_to_text(messages[1]) == "hi"
    summarizer = cli_kernel.resolve_service("context_summarizer")
    assert hasattr(summarizer, "summarize")


async def test_stats_and_tokens(cli_kernel):
    stats = cli_kernel.resolve_service("stats")
    stats.reset()
    stats.accumulate_usage({"input": 10, "output": 5})
    assert stats.token_stats()["input"] >= 10
    tokens = cli_kernel.resolve_service("tokens")
    assert tokens.estimate("abcd") >= 1


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


async def test_overlay_disable_runtime_data_service():
    kernel = await _build_with_disable(["runtime_data::tokens"])
    try:
        assert not kernel.has_service("tokens")
        assert kernel.has_service("stats")
    finally:
        await kernel.dispose()


async def test_loop_message_queue_kernel_first(cli_kernel):
    from src.app_loop._loop import _create_message_queue
    from src.core.message_queue import AsyncMessageQueue

    queue = _create_message_queue()
    assert isinstance(queue, AsyncMessageQueue)


async def test_loop_message_queue_fallback_without_kernel():
    from src.kernel import set_current_kernel

    set_current_kernel(None)
    from src.app_loop._loop import _create_message_queue
    from src.core.message_queue import AsyncMessageQueue

    assert isinstance(_create_message_queue(), AsyncMessageQueue)


async def test_model_async_upload_kernel_first(cli_kernel, monkeypatch):
    import src.api.model_async as ma

    seen = []

    def _fake(messages):
        seen.append("kernel")
        return {"folded": 0}

    monkeypatch.setattr(cli_kernel.resolve_service("multimodal"), "optimize_messages_for_upload", _fake)
    ma._optimize_messages_for_upload([{"role": "user"}])
    assert seen == ["kernel"]


def test_model_async_upload_fallback_without_kernel(monkeypatch):
    from src.kernel import set_current_kernel

    set_current_kernel(None)
    import src.api.model_async as ma

    seen = []

    def _fake(messages):
        seen.append("fallback")
        return {"folded": 0}

    monkeypatch.setattr(ma, "optimize_messages_for_upload", _fake)
    ma._optimize_messages_for_upload([{"role": "user"}])
    assert seen == ["fallback"]
