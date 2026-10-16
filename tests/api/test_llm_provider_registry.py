"""LLM Provider 注册表测试 — 模型适配器的插件化注册与路由。

覆盖：
- 内置 provider 路由行为与既有 get_adapter 契约一致；
- 注册 / 覆盖 / 撤销（同名恢复旧条目）；
- 前缀 / 子串 / fallback 匹配；
- 内核 model bundle 的内置 provider 插件注册；
- 插件 Fiber 卸载时撤销其注册的 provider。
"""

from __future__ import annotations

import pytest

import src.api._adapter_manager as am
from src.api.provider_registry import (
    LlmProviderRegistry,
    default_registry,
    reset_default_registry,
)
from src.plugins.bootstrap import build_kernel, shutdown_kernel


@pytest.fixture(autouse=True)
def clean_registry():
    reset_default_registry()
    am._adapter_cache.clear()
    yield
    reset_default_registry()
    am._adapter_cache.clear()


@pytest.fixture
async def minimal_kernel():
    kernel = await build_kernel("minimal")
    try:
        yield kernel
    finally:
        await shutdown_kernel(kernel)


def test_get_adapter_builtin_routing():
    from src.api.adapters import DeepSeekAdapter, OpenAICompatAdapter
    from src.api.adapters.anthropic import AnthropicAdapter
    from src.api.adapters.ollama import OllamaAdapter

    assert isinstance(am.get_adapter("deepseek-chat"), DeepSeekAdapter)
    assert isinstance(am.get_adapter("DeepSeek-V4"), DeepSeekAdapter)
    assert isinstance(am.get_adapter("claude-3-5-sonnet"), AnthropicAdapter)
    assert isinstance(am.get_adapter("anthropic/claude-3"), AnthropicAdapter)
    assert isinstance(am.get_adapter("ollama/llama3"), OllamaAdapter)
    assert isinstance(am.get_adapter("gpt-4o"), OpenAICompatAdapter)


def test_get_adapter_cache_and_distinct_models():
    a1 = am.get_adapter("deepseek-chat")
    a2 = am.get_adapter("deepseek-chat")
    assert a1 is a2
    assert am.get_adapter("gpt-4o") is not am.get_adapter("gpt-4-turbo")


def test_registry_register_and_match():
    registry = LlmProviderRegistry()

    class Fake:
        pass

    registry.register("fakeprov", lambda: Fake(), prefixes=("my-model",))
    assert registry.match_name("my-model-1") == "fakeprov"
    assert isinstance(registry.resolve("my-model-1"), Fake)


def test_registry_same_name_override_and_undo():
    registry = LlmProviderRegistry()

    class First:
        pass

    class Second:
        pass

    registry.register("p", lambda: First(), fallback=True)
    undo = registry.register("p", lambda: Second(), fallback=True)
    assert isinstance(registry.resolve("unknown-model"), Second)
    undo()
    assert isinstance(registry.resolve("unknown-model"), First)
    assert registry.names().count("p") == 1


def test_registry_fallback_and_unregister():
    registry = LlmProviderRegistry()

    class Fallback:
        pass

    registry.register("fallback", lambda: Fallback(), fallback=True)
    assert registry.match_name("whatever") == "fallback"
    assert registry.unregister("fallback") is True
    with pytest.raises(ValueError):
        registry.resolve("whatever")


def test_registry_substring_match():
    registry = LlmProviderRegistry()

    class Sub:
        pass

    registry.register("sub", lambda: Sub(), substrings=("claude",))
    assert registry.match_name("some-claude-model") == "sub"


def test_registry_rejects_bad_arguments():
    registry = LlmProviderRegistry()
    with pytest.raises(ValueError):
        registry.register("", lambda: None)
    with pytest.raises(TypeError):
        registry.register("bad", object())


def test_default_registry_has_builtin_providers():
    names = default_registry().names()
    for expected in ("deepseek", "anthropic", "ollama", "openai_compat"):
        assert expected in names


async def test_kernel_model_bundle_registers_providers(minimal_kernel):
    llm = minimal_kernel.resolve_service("llm")
    names = llm.provider_names()
    for expected in ("deepseek", "anthropic", "ollama", "openai_compat"):
        assert expected in names
    assert llm.resolve_provider("deepseek-chat") == "deepseek"
    assert llm.resolve_provider("claude-3") == "anthropic"
    assert llm.resolve_provider("ollama/llama3") == "ollama"
    assert llm.resolve_provider("gpt-4o") == "openai_compat"

    from src.api.adapters import DeepSeekAdapter

    assert isinstance(llm.adapter("deepseek-chat"), DeepSeekAdapter)


async def test_plugin_fiber_unload_revokes_provider(minimal_kernel):
    from src.kernel import plugin

    class Ext:
        pass

    @plugin("ext_llm", inject=["llm"])
    def apply(ctx):
        undo = ctx.llm.register_provider("ext", lambda: Ext(), prefixes=("ext-model",))
        ctx.effect(lambda: undo)

    fiber = minimal_kernel.mount(apply)
    await minimal_kernel.settle()
    llm = minimal_kernel.resolve_service("llm")
    try:
        assert llm.resolve_provider("ext-model-1") == "ext"
        assert isinstance(llm.adapter("ext-model-1"), Ext)
    finally:
        await fiber.dispose()
        await minimal_kernel.settle()
    assert llm.resolve_provider("ext-model-1") == "openai_compat"
    assert "ext" not in llm.provider_names()
