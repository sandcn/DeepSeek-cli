"""注册表基类 — 统一 register/resolve/list_registered 接口。

所有注册表类（ComponentRegistry, EventHandlerRegistry 等）
应继承此类并实现抽象方法。

设计原则：
  - 模板方法模式：RegistryBase 定义注册/解析骨架，子类实现具体存储和线程安全细节
  - 最小接口：仅定义 register/resolve/list_registered 三个方法
  - 向后兼容：子类已有的 register/resolve 签名保持不变

用法::

    from .registry_base import RegistryBase

    class MyRegistry(RegistryBase):
        def register(self, key, value) -> None:
            ...
        def resolve(self, key):
            ...
"""

from __future__ import annotations

from abc import ABC, abstractmethod


__all__: list[str] = [
    "RegistryBase",
]


class RegistryBase(ABC):
    """注册表基类 — 统一 register/resolve/list_registered 接口。

    所有注册表类（ComponentRegistry, EventHandlerRegistry 等）
    应继承此类并实现抽象方法。

    提供默认的 ``list_registered()`` 实现（返回空字典），子类可覆盖
    以返回线程安全的快照副本。
    """

    @abstractmethod
    def register(self, key, value) -> None:
        """注册键值映射。

        Args:
            key: 注册键（如 command_id int / event_type type 等）。
            value: 注册值（如 method_name str / handler_name str 等）。
        """
        ...

    @abstractmethod
    def resolve(self, key):
        """解析键对应的值。

        Args:
            key: 注册键。

        Returns:
            注册值，未注册时返回 None。
        """
        ...

    def list_registered(self) -> dict:
        """返回所有已注册映射的副本（线程安全）。

        默认实现返回空字典。子类可覆盖以返回线程安全的快照。

        Returns:
            注册映射的字典副本。
        """
        return {}
