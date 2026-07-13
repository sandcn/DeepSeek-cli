"""缓存适配器 — 缓存端口实现的重导出层

LRUCache 和 NullCache 作为核心层内置实现直接定义于 src.core.cache 中，
此模块作为适配器层的重导出入口，便于通过 adapters 包统一访问所有端口实现。
"""
from __future__ import annotations

from ..cache import LRUCache, NullCache

__all__ = [
    "LRUCache",
    "NullCache",
]
