"""输出端口 — 核心层控制台输出接口"""
from __future__ import annotations
from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import Any
import threading


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


# ═══════════════════════════════════════════════════════════════
# 默认输出适配器 — 包装 ui.events.publish_output
# ═══════════════════════════════════════════════════════════════

class DefaultOutputAdapter(OutputPort):
    """默认输出适配器 — 委托给 ui.events.publish_output

    作为全局默认输出端口，供核心模块在没有依赖注入时使用。
    """

    def __init__(self):
        self._lock = None

    def _get_lock(self):
        if self._lock is None:
            from ...ui._lock import output_lock
            self._lock = output_lock
        return self._lock

    def write(self, text: str, level: str = "info", source: str = "core") -> None:
        from ...ui.events import publish_output
        publish_output(text, level=level, source=source)

    def write_with_lock(self, text: str, level: str = "info", source: str = "core") -> None:
        lock = self._get_lock()
        with lock:
            from ...ui.events import publish_output
            publish_output(text, level=level, source=source)

    @contextmanager
    def locked(self):
        lock = self._get_lock()
        with lock:
            yield


# ── 模块级全局输出端口 ───────────────────────────────────
_default_output_port: OutputPort | None = None
_output_port_lock = threading.RLock()


def get_default_output_port() -> OutputPort:
    """获取全局默认输出端口（线程安全单例）

    核心模块通过此函数获取输出端口，替代直接 import publish_output。
    在无显式注入时提供默认实现（包装 publish_output）。
    """
    global _default_output_port
    if _default_output_port is None:
        with _output_port_lock:
            if _default_output_port is None:
                _default_output_port = DefaultOutputAdapter()
    return _default_output_port


def set_default_output_port(port: OutputPort) -> None:
    """设置全局默认输出端口（用于测试/依赖注入）"""
    global _default_output_port
    with _output_port_lock:
        _default_output_port = port


def reset_default_output_port() -> None:
    """重置全局默认输出端口（主要用于测试）"""
    global _default_output_port
    with _output_port_lock:
        _default_output_port = None



