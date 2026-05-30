"""LLM 适配器 — 封装不同提供商的 API 差异

内置适配器:
- OpenAICompatAdapter — OpenAI 兼容 API（OpenAI / GLM / 通用）
- DeepSeekAdapter     — DeepSeek API（V4 thinking mode / reasoner / classic）
- AnthropicAdapter    — Anthropic API（Claude 系列模型）
- OllamaAdapter       — Ollama 本地模型
"""
from .base import BaseLLMAdapter
from .openai_compat import OpenAICompatAdapter
from .deepseek import DeepSeekAdapter
from .anthropic import AnthropicAdapter
from .ollama import OllamaAdapter

__all__ = [
    "BaseLLMAdapter",
    "OpenAICompatAdapter",
    "DeepSeekAdapter",
    "AnthropicAdapter",
    "OllamaAdapter",
]
