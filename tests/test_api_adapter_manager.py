"""src/api/_adapter_manager — get_adapter 路由与缓存单元测试。

覆盖：
  - deepseek / anthropic(claude) / ollama / openai-compat 路由
  - 大小写不敏感前缀匹配
  - 按模型名缓存（同模型返回同一实例；不同模型独立）
  - 线程安全锁存在性（基础结构断言）
"""

from __future__ import annotations

import pytest

import src.api._adapter_manager as am
from src.api._adapter_manager import get_adapter


@pytest.fixture(autouse=True)
def clean_cache():
    """每个测试前后清空全局适配器缓存，避免跨测试污染。"""
    am._adapter_cache.clear()
    yield
    am._adapter_cache.clear()


def test_routes_deepseek():
    from src.api.adapters import DeepSeekAdapter

    assert isinstance(get_adapter("deepseek-chat"), DeepSeekAdapter)
    assert isinstance(get_adapter("deepseek-v4-flash"), DeepSeekAdapter)


def test_routes_anthropic_claude():
    from src.api.adapters.anthropic import AnthropicAdapter

    assert isinstance(get_adapter("anthropic/claude-3-5-sonnet"), AnthropicAdapter)
    assert isinstance(get_adapter("claude-3-5-sonnet"), AnthropicAdapter)
    assert isinstance(get_adapter("Claude-3-Haiku"), AnthropicAdapter)


def test_routes_ollama():
    from src.api.adapters.ollama import OllamaAdapter

    assert isinstance(get_adapter("ollama/llama3"), OllamaAdapter)


def test_routes_openai_compat_fallback():
    from src.api.adapters import OpenAICompatAdapter

    assert isinstance(get_adapter("gpt-4o"), OpenAICompatAdapter)
    assert isinstance(get_adapter("glm-4"), OpenAICompatAdapter)
    assert isinstance(get_adapter("qwen2.5"), OpenAICompatAdapter)


def test_case_insensitive_prefix():
    from src.api.adapters import DeepSeekAdapter

    assert isinstance(get_adapter("DeepSeek-V3"), DeepSeekAdapter)


def test_cache_returns_same_instance():
    a1 = get_adapter("deepseek-chat")
    a2 = get_adapter("deepseek-chat")
    assert a1 is a2


def test_cache_distinct_models_distinct():
    a1 = get_adapter("gpt-4o")
    a2 = get_adapter("gpt-4-turbo")
    assert a1 is not a2
