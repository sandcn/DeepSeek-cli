"""输出适配器 — 默认输出实现

职责：桥接核心层与基础设施层（tui.events / renderer._locks）。
适配器层允许导入 tui/ 模块（这是适配器层的职责——桥接核心与基础设施）。
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Optional
import threading

from ..ports import OutputPort


class DefaultOutputAdapter(OutputPort):
    """默认输出适配器 — 经 get_output_publisher 工厂委托给 publish_output

    作为全局默认输出端口，供核心模块在没有依赖注入时使用。
    适配器层允许导入桥接模块（display_target Protocol）。
    """

    def __init__(self):
        self._lock: Optional[threading.RLock] = None

    def _get_lock(self):
        if self._lock is None:
            from ...renderer._locks import render_lock
            self._lock = render_lock
        return self._lock

    def write(self, text: str, level: str = "info", source: str = "core") -> None:
        """输出文本到终端（无锁）"""
        self._publish(text, level=level, source=source)

    def write_with_lock(self, text: str, level: str = "info", source: str = "core") -> None:
        """持有输出锁写入文本到终端"""
        lock = self._get_lock()
        with lock:
            self._publish(text, level=level, source=source)

    def _publish(self, text: str, level: str = "info", source: str = "core") -> None:
        """经 get_output_publisher 工厂发布输出事件（依赖倒置：core 定义 Protocol、tui 实现）。

        无头 None 判定已移除（P0 修复）：工厂始终返回可调用的 publish_output，
        输出经 OutputConsumer 兜底直写终端（原链路保留）；``if publisher is not None``
        保留为防御性判断以兼容未来返回 None 场景。
        """
        from ..display_target import get_output_publisher
        publisher = get_output_publisher()
        if publisher is not None:
            publisher(text, level=level, source=source)

    @contextmanager
    def locked(self):
        lock = self._get_lock()
        with lock:
            yield

    # ── 类级默认实例管理（替代模块级可变状态） ──────────
    _default_instance: DefaultOutputAdapter | None = None
    _default_instance_lock = threading.RLock()

    @classmethod
    def get_default(cls) -> DefaultOutputAdapter:
        """获取全局默认输出端口实例（线程安全单例）"""
        if cls._default_instance is None:
            with cls._default_instance_lock:
                if cls._default_instance is None:
                    cls._default_instance = cls()
        return cls._default_instance

    @classmethod
    def set_default(cls, port: DefaultOutputAdapter) -> None:
        """设置全局默认输出端口实例（用于测试/依赖注入）"""
        with cls._default_instance_lock:
            cls._default_instance = port

    @classmethod
    def reset_default(cls) -> None:
        """重置全局默认输出端口实例（主要用于测试）"""
        with cls._default_instance_lock:
            cls._default_instance = None


# ── 向后兼容导出别名（@deprecated: 请使用 DefaultOutputAdapter 类方法） ──
def get_default_output_port() -> DefaultOutputAdapter:
    """获取全局默认输出端口（已废弃，请使用 DefaultOutputAdapter.get_default()）"""
    return DefaultOutputAdapter.get_default()


def set_default_output_port(port: DefaultOutputAdapter) -> None:
    """设置全局默认输出端口（已废弃，请使用 DefaultOutputAdapter.set_default()）"""
    DefaultOutputAdapter.set_default(port)


def reset_default_output_port() -> None:
    """重置全局默认输出端口（已废弃，请使用 DefaultOutputAdapter.reset_default()）"""
    DefaultOutputAdapter.reset_default()
