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
        "thinking_enabled": True,
    }
    monkeypatch.setattr("src.config.loader.get_rc", lambda: rc)
    return rc


def test_deepseek_uses_profile_temperature_and_effort(monkeypatch):
    """思考模式开启时温度不生效（官方文档），故只校验思考级别；关闭后才带温度。"""
    rc = _install_rc(monkeypatch, {
        "model": "deepseek-v4-pro", "provider": "deepseek",
        "temperature": "0.7", "reasoning_effort": "high",
    })
    kwargs = DeepSeekAdapter().build_request_kwargs([], "deepseek-v4-pro")
    assert "temperature" not in kwargs
    assert kwargs["thinking"] == {"type": "enabled", "reasoning_effort": "high"}
    rc["thinking_enabled"] = False
    kwargs = DeepSeekAdapter().build_request_kwargs([], "deepseek-v4-pro")
    assert kwargs["temperature"] == 0.7


def test_deepseek_empty_fields_fall_back_to_global(monkeypatch):
    rc = _install_rc(monkeypatch, {
        "model": "deepseek-v4-pro", "provider": "deepseek",
    })
    kwargs = DeepSeekAdapter().build_request_kwargs([], "deepseek-v4-pro")
    assert "temperature" not in kwargs
    assert kwargs["thinking"]["reasoning_effort"] == "max"
    rc["thinking_enabled"] = False
    kwargs = DeepSeekAdapter().build_request_kwargs([], "deepseek-v4-pro")
    assert kwargs["temperature"] == 0.2


def test_openai_compat_uses_profile_values(monkeypatch):
    rc = _install_rc(monkeypatch, {
        "model": "deepseek-v4-pro", "provider": "deepseek",
        "temperature": "1.1", "reasoning_effort": "low",
    })
    kwargs = OpenAICompatAdapter().build_request_kwargs([], "deepseek-v4-pro")
    assert "temperature" not in kwargs
    assert kwargs["thinking"]["reasoning_effort"] == "low"
    rc["thinking_enabled"] = False
    kwargs = OpenAICompatAdapter().build_request_kwargs([], "deepseek-v4-pro")
    assert kwargs["temperature"] == 1.1


def test_deepseek_thinking_disabled_by_global_switch(monkeypatch):
    """Ctrl+T 关闭思考模式 → 请求显式注入 thinking.type=disabled（温度生效）。"""
    rc = _install_rc(monkeypatch, {
        "model": "deepseek-v4-pro", "provider": "deepseek",
        "reasoning_effort": "high", "temperature": "0.6",
    })
    rc["thinking_enabled"] = False
    kwargs = DeepSeekAdapter().build_request_kwargs([], "deepseek-v4-pro")
    assert kwargs["thinking"] == {"type": "disabled"}
    assert kwargs["temperature"] == 0.6


def test_openai_compat_thinking_disabled_by_global_switch(monkeypatch):
    rc = _install_rc(monkeypatch, {
        "model": "deepseek-v4-pro", "provider": "deepseek",
        "reasoning_effort": "low", "temperature": "0.45",
    })
    rc["thinking_enabled"] = False
    kwargs = OpenAICompatAdapter().build_request_kwargs([], "deepseek-v4-pro")
    assert kwargs["thinking"] == {"type": "disabled"}
    assert kwargs["temperature"] == 0.45


def test_ollama_uses_profile_temperature(monkeypatch):
    rc = _install_rc(monkeypatch, {
        "model": "qwen2.5", "provider": "custom",
        "temperature": "0.55",
    })
    rc["thinking_enabled"] = False
    kwargs = OllamaAdapter().build_request_kwargs([], "qwen2.5")
    assert kwargs["temperature"] == 0.55


def test_anthropic_uses_profile_temperature(monkeypatch):
    rc = _install_rc(monkeypatch, {
        "model": "claude-sonnet-5-5", "provider": "anthropic",
        "temperature": "0.8",
    })
    rc["thinking_enabled"] = False
    kwargs = AnthropicAdapter().build_request_kwargs([], "claude-sonnet-5-5")
    assert kwargs["temperature"] == 0.8


def test_anthropic_clamps_profile_temperature(monkeypatch):
    rc = _install_rc(monkeypatch, {
        "model": "claude-sonnet-5-5", "provider": "anthropic",
        "temperature": "1.8",
    })
    rc["thinking_enabled"] = False
    kwargs = AnthropicAdapter().build_request_kwargs([], "claude-sonnet-5-5")
    assert kwargs["temperature"] == 1.0


@pytest.mark.parametrize("adapter_factory", [
    DeepSeekAdapter, OpenAICompatAdapter, OllamaAdapter, AnthropicAdapter,
])
def test_invalid_profile_values_fall_back(monkeypatch, adapter_factory):
    rc = _install_rc(monkeypatch, {
        "model": "deepseek-v4-pro", "provider": "deepseek",
        "temperature": "nan", "reasoning_effort": "ultra",
    })
    rc["thinking_enabled"] = False
    kwargs = adapter_factory().build_request_kwargs([], "deepseek-v4-pro")
    assert kwargs["temperature"] == 0.2


# ── 温度是否真正生效（DeepSeek 思考模式不支持 temperature） ──────────

@pytest.mark.parametrize("adapter_factory", [
    DeepSeekAdapter, OpenAICompatAdapter, OllamaAdapter, AnthropicAdapter,
])
def test_temperature_omitted_while_thinking_enabled(monkeypatch, adapter_factory):
    """思考模式开启 → 温度被 DeepSeek 忽略，请求体不再携带 temperature。"""
    rc = _install_rc(monkeypatch, {
        "model": "deepseek-v4-pro", "provider": "deepseek",
        "temperature": "0.8",
    })
    rc["thinking_enabled"] = True
    kwargs = adapter_factory().build_request_kwargs([], "deepseek-v4-pro")
    assert "temperature" not in kwargs


@pytest.mark.parametrize("adapter_factory", [
    DeepSeekAdapter, OpenAICompatAdapter, OllamaAdapter, AnthropicAdapter,
])
def test_temperature_sent_after_thinking_disabled(monkeypatch, adapter_factory):
    """Ctrl+T 关闭思考模式 → 温度重新携带并生效（档案值优先）。"""
    rc = _install_rc(monkeypatch, {
        "model": "deepseek-v4-pro", "provider": "deepseek",
        "temperature": "0.8",
    })
    rc["thinking_enabled"] = False
    kwargs = adapter_factory().build_request_kwargs([], "deepseek-v4-pro")
    assert kwargs["temperature"] == 0.8


def test_openai_compat_temperature_omitted_while_thinking_enabled(monkeypatch):
    """OpenAI 兼容适配器的 V4 分支同样省略 temperature，且仍注入 thinking。"""
    rc = _install_rc(monkeypatch, {
        "model": "deepseek-v4-pro", "provider": "deepseek",
        "temperature": "1.1", "reasoning_effort": "low",
    })
    rc["thinking_enabled"] = True
    kwargs = OpenAICompatAdapter().build_request_kwargs([], "deepseek-v4-pro")
    assert "temperature" not in kwargs
    assert kwargs["thinking"] == {"type": "enabled", "reasoning_effort": "low"}
