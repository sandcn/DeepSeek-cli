"""单例元类 — 消除单例模板代码（核心层通用工具）。

为使用该元类的类自动注入 ``_instance`` / ``_instance_lock`` 类变量，
并提供线程安全的 ``get_default()``（双重检查锁）与 ``reset_default()``。
继承自 ``ABCMeta``，可与抽象类共存。

依赖方向：零依赖纯工具，位于核心层（表现层 ``tui.core.singleton`` 仅
re-export 兼容）。
"""

from __future__ import annotations

import threading
from abc import ABCMeta
from typing import Any, ClassVar, Optional

__all__ = ["SingletonMeta"]


class SingletonMeta(ABCMeta):
    def __new__(mcs, name: str, bases: tuple, namespace: dict, **kwargs: Any) -> type:
        cls = super().__new__(mcs, name, bases, namespace, **kwargs)
        if "_instance" not in namespace:
            cls._instance: ClassVar[Optional[Any]] = None
        if "_instance_lock" not in namespace:
            cls._instance_lock: ClassVar[threading.Lock] = threading.Lock()
        return cls

    def get_default(cls: type) -> Any:
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def reset_default(cls: type) -> None:
        with cls._instance_lock:
            cls._instance = None
