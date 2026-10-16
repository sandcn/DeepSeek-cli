"""LLM provider 条目化测试 — 每个内置 provider 一个独立插件条目。

覆盖：
- 清单为每个内置 provider 声明独立条目（可 patch/overlay）；
- 默认 profile 下 ctx.llm 的 provider 由条目注册（默认装配被抑制）；
- overlay 禁用单个 provider 真正生效（模型回落到匹配项）；
- 注册表 active/disabled 语义。
"""

from __future__ import annotations

import pytest

import src.api._adapter_manager as am
from src.kernel import Kernel, materialize
from src.plugins.bootstrap import (
    build_kernel,
    inject_managed_config,
    resolve_entries,
    shutdown_kernel,
)
from src.plugins.manifest import build_config_tree


@pytest.fixture(autouse=True)
def _clean_adapter_cache():
    am.clear_adapter_cache()
    yield
    am.clear_adapter_cache()


def _resolved(profile="cli"):
    return materialize(resolve_entries(profile, discover_external=False))


def test_manifest_declares_each_builtin_provider():
    from src.api.provider_registry import builtin_provider_names

    declared = []
    for entry, plug in _resolved():
        if getattr(plug, "name", "").startswith("llm_provider_"):
            declared.append((entry.config or {}).get("name"))
    assert set(declared) == set(builtin_provider_names())
    assert len(declared) == len(builtin_provider_names())


def test_tree_declares_model_provider_entries():
    tree = build_config_tree()
    resolved = tree.resolve("cli")
    entry_ids = {entry.id for entry in resolved}
    for name in ("deepseek", "anthropic", "ollama", "openai_compat"):
        assert f"model::llm_provider_{name}" in entry_ids


async def test_default_profile_providers_by_manifest():
    from src.api.provider_registry import builtin_provider_names

    kernel = await build_kernel("cli")
    try:
        llm = kernel.resolve_service("llm")
        assert set(llm.provider_names()) == set(builtin_provider_names())
        assert set(llm.managed_providers()) == set(builtin_provider_names())
        assert llm.disabled_providers() == []
        assert llm.resolve_provider("gpt-4o") == "openai_compat"
    finally:
        await shutdown_kernel(kernel)


async def _build_with_disable(ids):
    from src.kernel import get_current_kernel, set_current_kernel
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
    set_current_kernel(kernel)
    am.clear_adapter_cache()
    return kernel


async def _shutdown(kernel):
    from src.kernel import get_current_kernel, set_current_kernel

    await kernel.dispose()
    if get_current_kernel() is kernel:
        set_current_kernel(None)
    am.clear_adapter_cache()


async def test_overlay_disable_prompt_mode():
    kernel = await _build_with_disable(["model::llm_provider_deepseek"])
    try:
        llm = kernel.resolve_service("llm")
        assert "deepseek" not in llm.provider_names()
        assert llm.resolve_provider("deepseek-chat") == "openai_compat"

        from src.api.adapters import OpenAICompatAdapter

        assert isinstance(llm.adapter("deepseek-chat"), OpenAICompatAdapter)
    finally:
        await _shutdown(kernel)


async def test_overlay_disable_fallback_provider():
    kernel = await _build_with_disable(["model::llm_provider_openai_compat"])
    try:
        llm = kernel.resolve_service("llm")
        assert "openai_compat" not in llm.provider_names()
        assert llm.resolve_provider("deepseek-chat") == "deepseek"
    finally:
        await _shutdown(kernel)


def test_registry_active_and_disabled_semantics():
    from src.api.provider_registry import (
        active_builtin_provider_specs,
        build_default_registry,
        builtin_provider_names,
    )

    names = builtin_provider_names()
    assert len(active_builtin_provider_specs()) == len(names)
    assert active_builtin_provider_specs(managed=["deepseek"]) != active_builtin_provider_specs()
    managed_registry = build_default_registry(managed=["deepseek"])
    assert "deepseek" not in managed_registry.names()
    assert "openai_compat" in managed_registry.names()
    disabled_registry = build_default_registry(disabled=["ollama", "openai_compat"])
    assert disabled_registry.names() == ["deepseek", "anthropic"]
