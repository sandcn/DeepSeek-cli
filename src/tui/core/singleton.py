"""单例元类 — 兼容 re-export 层。

``SingletonMeta`` 实现已下沉至核心层 ``core.singleton``。本模块保持旧导入
路径 ``src.tui.core.singleton`` 兼容。
"""

from __future__ import annotations

from ...core.singleton import SingletonMeta  # noqa: F401

__all__ = ["SingletonMeta"]
