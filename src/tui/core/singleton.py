"""单例元类 — 消除单例模板代码。

提供 ``SingletonMeta`` 元类，为所有使用该元类的类自动添加：
  - ``_instance`` / ``_instance_lock`` 类变量
  - ``get_default()`` 类方法（双重检查锁 DCL）
  - ``reset_default()`` 类方法（线程安全重置）

继承自 ``ABCMeta``，可与抽象类共存。

用法::

    from .singleton import SingletonMeta

    class MyClass(metaclass=SingletonMeta):
        def __init__(self):
            ...

    inst = MyClass.get_default()       # 获取单例
    MyClass.reset_default()             # 重置单例（供测试使用）
"""

from __future__ import annotations

import threading
from abc import ABCMeta
from typing import Any, ClassVar, Optional


__all__: list[str] = [
    "SingletonMeta",
]


class SingletonMeta(ABCMeta):
    """单例元类 — 为使用该元类的类自动启用单例模式。

    继承自 ``ABCMeta``，可与抽象类（ABC）共存。
    每个子类拥有独立的 ``_instance`` 和 ``_instance_lock``。

    提供类方法：
      - ``get_default()`` — 线程安全单例获取（双重检查锁）
      - ``reset_default()`` — 线程安全单例重置
    """

    def __new__(mcs, name: str, bases: tuple, namespace: dict, **kwargs: Any) -> type:
        """创建类时自动注入 ``_instance`` 和 ``_instance_lock`` 类变量。

        Args:
            name: 类名。
            bases: 基类元组。
            namespace: 类命名空间。
            **kwargs: 额外关键字参数（如 metaclass 关键字参数）。

        Returns:
            创建的类。
        """
        cls = super().__new__(mcs, name, bases, namespace, **kwargs)
        # 为每个使用 SingletonMeta 的类注入独立的单例缓存和锁
        cls._instance: ClassVar[Optional[Any]] = None
        cls._instance_lock: ClassVar[threading.Lock] = threading.Lock()
        return cls

    def get_default(cls: type) -> Any:
        """获取单例实例（双重检查锁，线程安全）。

        首次调用时创建实例，后续调用返回同一实例。

        Args:
            cls: 调用方的类对象（由 Python 自动传递）。

        Returns:
            类 cls 的单例实例。
        """
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def reset_default(cls: type) -> None:
        """重置单例实例（线程安全）。

        将 ``_instance`` 设为 None，下次 ``get_default()`` 调用时重建新实例。
        供测试中的 ``setUp`` / ``tearDown`` 使用。

        Args:
            cls: 调用方的类对象（由 Python 自动传递）。
        """
        with cls._instance_lock:
            cls._instance = None
