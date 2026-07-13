"""缓存端口 — 核心层与缓存基础设施之间的抽象协议

定义 CachePort 抽象基类，覆盖 LRUCache、NullCache 等实现全部公有方法签名。
核心层通过此端口存取缓存数据，不直接依赖具体缓存实现。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional


class CachePort(ABC):
    """缓存端口 — 核心层通过此接口存取缓存数据"""

    @abstractmethod
    def get(self, key: str) -> Optional[Any]:
        """获取缓存值，不存在或已过期返回 None"""
        ...

    @abstractmethod
    def set(self, key: str, value: Any, ttl: float = 300) -> None:
        """设置缓存值

        Args:
            key: 缓存键
            value: 缓存值
            ttl: 过期时间（秒），默认 300
        """
        ...

    @abstractmethod
    def delete(self, key: str) -> bool:
        """删除缓存键，存在返回 True"""
        ...

    @abstractmethod
    def clear(self) -> None:
        """清空所有缓存"""
        ...

    @abstractmethod
    def has(self, key: str) -> bool:
        """检查键是否存在且未过期"""
        ...

    @abstractmethod
    def get_or_set(self, key: str, factory, ttl: float = 300) -> Any:
        """获取缓存值，不存在则通过 factory 创建并缓存

        Args:
            key: 缓存键
            factory: 无参可调用对象，值不存在时调用
            ttl: 过期时间（秒）

        Returns:
            缓存值或 factory() 的结果
        """
        ...

    @abstractmethod
    def invalidate_pattern(self, pattern: str) -> int:
        """按模式前缀批量失效缓存

        Args:
            pattern: 键前缀

        Returns:
            失效的键数量
        """
        ...
