"""输出端口 — 核心层与文本输出之间的抽象协议

定义 OutputPort 抽象基类，覆盖 DefaultOutputAdapter 全部公有方法签名。
核心层通过此端口向终端输出文本，不直接依赖 tui/ 具体实现模块。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import AbstractContextManager
from typing import Optional


class OutputPort(ABC):
    """输出端口 — 核心层文本输出功能的抽象接口

    定义核心层向终端输出文本、按级别分发、持有锁写入的契约。
    所有适配器实现应继承此抽象类并实现全部抽象方法。
    """

    @abstractmethod
    def write(self, text: str, level: str = "info", source: str = "core") -> None:
        """输出文本到终端（无锁）"""
        ...

    @abstractmethod
    def write_with_lock(self, text: str, level: str = "info", source: str = "core") -> None:
        """持有输出锁写入文本到终端"""
        ...

    @abstractmethod
    def locked(self) -> AbstractContextManager:
        """获取输出锁的上下文管理器"""
        ...
