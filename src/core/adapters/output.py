"""输出适配器 — 默认输出实现

职责：桥接核心层与基础设施层（ui.events / ui._lock）。
适配器层允许导入 ui/ 模块（这是适配器层的职责——桥接核心与基础设施）。
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Optional
import threading


class DefaultOutputAdapter:
    """默认输出适配器 — 委托给 ui.events.publish_output

    作为全局默认输出端口，供核心模块在没有依赖注入时使用。
    适配器层允许导入 ui/ 模块（桥接职责）。
    """

    def __init__(self):
        self._lock: Optional[threading.RLock] = None

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
_default_output_port: DefaultOutputAdapter | None = None
_output_port_lock = threading.RLock()


def get_default_output_port() -> DefaultOutputAdapter:
    """获取全局默认输出端口（线程安全单例）"""
    global _default_output_port
    if _default_output_port is None:
        with _output_port_lock:
            if _default_output_port is None:
                _default_output_port = DefaultOutputAdapter()
    return _default_output_port


def set_default_output_port(port: DefaultOutputAdapter) -> None:
    """设置全局默认输出端口（用于测试/依赖注入）"""
    global _default_output_port
    with _output_port_lock:
        _default_output_port = port


def reset_default_output_port() -> None:
    """重置全局默认输出端口（主要用于测试）"""
    global _default_output_port
    with _output_port_lock:
        _default_output_port = None
