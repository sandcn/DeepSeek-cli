"""API 适配器按模型档案读取温度 / 思考级别测试。

2026-10-17 用户需求：/models 中每个模型可独立设置温度与思考级别（空值
沿用全局默认）；API 请求参数按当前生效档案取值，全局 /temperature 与
/reasoning 命令已删除。
"""

from __future__ import annotations

import pytest

from src.api.adapters.anthropic import AnthropicAdapter
from src.api.adapters.deepseek import DeepSeekAdapter
from src.api.adapters.ollama import OllamaAdapter
from src.api.adapters.openai_compat import OpenAICompatAdapter


def _install_rc(monkeypatch, profile):
    rc = {
        "model_profiles": [profile],
        "active_model_profile": 0,
        "temperature": 0.2,
        "reasoning_effort": "max",
    }
    monkeypatch.setattr("src.config.loader.get_rc", lambda: rc)
    return rc


def test_deepseek_uses_profile_temperature_and_effort(monkeypatch):
    _install_rc(monkeypatch, {
        "model": "deepseek-v4-pro", "provider": "deepseek",
        "temperature": "0.7", "reasoning_effort": "high",
    })
    kwargs = DeepSeekAdapter().build_request_kwargs([], "deepseek-v4-pro")
    assert kwargs["temperature"] == 0.7
    assert kwargs["thinking"] == {"type": "enabled", "reasoning_effort": "high"}


def test_deepseek_empty_fields_fall_back_to_global(monkeypatch):
    _install_rc(monkeypatch, {
        "model": "deepseek-v4-pro", "provider": "deepseek",
    })
    kwargs = DeepSeekAdapter().build_request_kwargs([], "deepseek-v4-pro")
    assert kwargs["temperature"] == 0.2
    assert kwargs["thinking"]["reasoning_effort"] == "max"


def test_openai_compat_uses_profile_values(monkeypatch):
    _install_rc(monkeypatch, {
        "model": "deepseek-v4-pro", "provider": "deepseek",
        "temperature": "1.1", "reasoning_effort": "low",
    })
    kwargs = OpenAICompatAdapter().build_request_kwargs([], "deepseek-v4-pro")
    assert kwargs["temperature"] == 1.1
    assert kwargs["thinking"]["reasoning_effort"] == "low"


def test_ollama_uses_profile_temperature(monkeypatch):
    _install_rc(monkeypatch, {
        "model": "qwen2.5", "provider": "custom",
        "temperature": "0.55",
    })
    kwargs = OllamaAdapter().build_request_kwargs([], "qwen2.5")
    assert kwargs["temperature"] == 0.55


def test_anthropic_uses_profile_temperature(monkeypatch):
    _install_rc(monkeypatch, {
        "model": "claude-sonnet-5-5", "provider": "anthropic",
        "temperature": "0.8",
    })
    kwargs = AnthropicAdapter().build_request_kwargs([], "claude-sonnet-5-5")
    assert kwargs["temperature"] == 0.8


def test_anthropic_clamps_profile_temperature(monkeypatch):
    _install_rc(monkeypatch, {
        "model": "claude-sonnet-5-5", "provider": "anthropic",
        "temperature": "1.8",
    })
    kwargs = AnthropicAdapter().build_request_kwargs([], "claude-sonnet-5-5")
    assert kwargs["temperature"] == 1.0


@pytest.mark.parametrize("adapter_factory", [
    DeepSeekAdapter, OpenAICompatAdapter, OllamaAdapter, AnthropicAdapter,
])
def test_invalid_profile_values_fall_back(monkeypatch, adapter_factory):
    _install_rc(monkeypatch, {
        "model": "deepseek-v4-pro", "provider": "deepseek",
        "temperature": "nan", "reasoning_effort": "ultra",
    })
    kwargs = adapter_factory().build_request_kwargs([], "deepseek-v4-pro")
    assert kwargs["temperature"] == 0.2
