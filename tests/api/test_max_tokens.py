"""API 层 ``max_tokens`` 透传：``0`` = 不限制（2026-10-22 用户需求）。

- OpenAI 兼容 provider：``0`` / ``None`` 均不写 ``max_tokens``（交给服务端）；
- Anthropic：``max_tokens`` 必填——``None`` 保持 4096 保守默认，``0`` 使用
  最大输出常量，正数使用调用方指定值。
"""

from __future__ import annotations

from src.api.adapters.anthropic import (
    AnthropicAdapter,
    _DEFAULT_MAX_OUTPUT_TOKENS,
    _UNLIMITED_MAX_OUTPUT_TOKENS,
)
from src.api.adapters.deepseek import DeepSeekAdapter

_MESSAGES = [{"role": "user", "content": "hi"}]


def test_openai_compatible_omits_max_tokens_when_unlimited():
    adapter = DeepSeekAdapter()
    kwargs = adapter.build_request_kwargs(
        _MESSAGES, "deepseek-v4-pro", max_tokens=0,
    )
    assert "max_tokens" not in kwargs

    kwargs = adapter.build_request_kwargs(
        _MESSAGES, "deepseek-v4-pro", max_tokens=1234,
    )
    assert kwargs["max_tokens"] == 1234


def test_anthropic_default_is_unchanged():
    adapter = AnthropicAdapter()
    kwargs = adapter.build_request_kwargs(_MESSAGES, "claude-opus-5-5")
    assert kwargs["max_tokens"] == _DEFAULT_MAX_OUTPUT_TOKENS


def test_anthropic_unlimited_uses_max_output_constant():
    adapter = AnthropicAdapter()
    kwargs = adapter.build_request_kwargs(
        _MESSAGES, "claude-opus-5-5", max_tokens=0,
    )
    assert kwargs["max_tokens"] == _UNLIMITED_MAX_OUTPUT_TOKENS


def test_anthropic_positive_value_is_used_as_is():
    adapter = AnthropicAdapter()
    kwargs = adapter.build_request_kwargs(
        _MESSAGES, "claude-opus-5-5", max_tokens=2048,
    )
    assert kwargs["max_tokens"] == 2048
