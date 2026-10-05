"""适配器管理 — 按模型名缓存和路由到对应 LLM 适配器。

路由规则委托给 :mod:`src.api.provider_registry`（provider 注册表）：
内置 provider（deepseek / anthropic / ollama / openai_compat）与外部插件
注册的 provider 走同一套匹配、覆盖、撤销机制。本模块只负责「按模型名
缓存适配器实例」，并依据注册表版本号使缓存失效（单向依赖，无循环导入）。
"""
from __future__ import annotations

import logging
import threading
from typing import Any

_logger = logging.getLogger(__name__)

# 适配器缓存（线程安全）
_adapter_cache: dict[str, Any] = {}
_adapter_cache_lock = threading.RLock()
#: 缓存对应的 provider 注册表版本（版本变化时整体失效）
_cache_version = -1


def get_adapter(model: str) -> Any:
    """根据模型名获取适配器（带缓存，线程安全）。"""
    global _cache_version
    from .provider_registry import default_registry

    registry = default_registry()
    with _adapter_cache_lock:
        version = registry.version
        if version != _cache_version:
            _adapter_cache.clear()
            _cache_version = version
        if model in _adapter_cache:
            return _adapter_cache[model]
        adapter = registry.resolve(model)
        _adapter_cache[model] = adapter
        return adapter


def clear_adapter_cache() -> None:
    """清空适配器缓存（provider 注册表变更后同步调用）。"""
    global _cache_version
    with _adapter_cache_lock:
        _adapter_cache.clear()
        _cache_version = -1


__all__ = ["get_adapter", "clear_adapter_cache"]
