"""输出端口 — 核心层控制台输出接口"""
from __future__ import annotations
import warnings
from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import Any


class OutputPort(ABC):
    """抽象输出端口

    核心层通过此接口输出文本（无锁/有锁）。
    """

    @abstractmethod
    def write(self, text: str, level: str = "info", source: str = "core") -> None:
        """写入输出"""
        ...

    @abstractmethod
    def write_with_lock(self, text: str, level: str = "info", source: str = "core") -> None:
        """带锁写入输出"""
        ...

    @abstractmethod
    @contextmanager
    def locked(self):
        """输出锁上下文管理器 — 在多线程环境中同步输出

        用法:
            with output_port.locked():
                # 需要同步的代码块
        """
        ...


# ── 兼容层：委派到 adapters/output ─────────────────────
# 这些函数保留在此处保持向后兼容，实际实现在 adapters/output.py 中
# 新代码应直接使用 OutputPort 抽象，或从 adapters.output 导入具体实现


def get_default_output_port() -> OutputPort:
    """获取全局默认输出端口（委派到 adapters/output）"""
    warnings.warn(
        "get_default_output_port() 已废弃，请从 src.core.adapters.output 导入",
        DeprecationWarning, stacklevel=2,
    )
    from ..adapters.output import get_default_output_port as _impl
    return _impl()


def set_default_output_port(port: OutputPort) -> None:
    """设置全局默认输出端口（委派到 adapters/output）"""
    warnings.warn(
        "set_default_output_port() 已废弃，请从 src.core.adapters.output 导入",
        DeprecationWarning, stacklevel=2,
    )
    from ..adapters.output import set_default_output_port as _impl
    _impl(port)


def reset_default_output_port() -> None:
    """重置全局默认输出端口（委派到 adapters/output）"""
    warnings.warn(
        "reset_default_output_port() 已废弃，请从 src.core.adapters.output 导入",
        DeprecationWarning, stacklevel=2,
    )
    from ..adapters.output import reset_default_output_port as _impl
    _impl()
