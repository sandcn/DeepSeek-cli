"""适配器管理 — 按模型名缓存和路由到对应 LLM 适配器"""
from __future__ import annotations

import logging
import threading
from typing import Any

_logger = logging.getLogger(__name__)

# 适配器缓存（线程安全）
_adapter_cache: dict[str, Any] = {}
_adapter_cache_lock = threading.Lock()


def get_adapter(model: str) -> Any:
    """根据模型名获取适配器（带缓存，线程安全）。"""
    with _adapter_cache_lock:
        if model in _adapter_cache:
            return _adapter_cache[model]

        # 按前缀匹配，长前缀优先避免歧义
        model_lower = model.lower()
        if model_lower.startswith("deepseek"):
            from .adapters import DeepSeekAdapter
            _adapter_cache[model] = DeepSeekAdapter()
        elif model_lower.startswith("anthropic") or "claude" in model_lower:
            from .adapters.anthropic import AnthropicAdapter
            _adapter_cache[model] = AnthropicAdapter()
        elif model_lower.startswith("ollama"):
            from .adapters.ollama import OllamaAdapter
            _adapter_cache[model] = OllamaAdapter()
        else:
            from .adapters import OpenAICompatAdapter
            _adapter_cache[model] = OpenAICompatAdapter()
    return _adapter_cache[model]


def clear_adapter_cache() -> None:
    """清空适配器缓存（用于测试）"""
    with _adapter_cache_lock:
        _adapter_cache.clear()
