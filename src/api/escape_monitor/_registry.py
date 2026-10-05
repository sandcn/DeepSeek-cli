"""活跃 EscapeMonitor 注册表 — 活跃实例的单例真源与内核服务接入点。

「一切皆插件」：活跃 EscapeMonitor 的单例状态从 ``_monitor`` 模块全局上移为
本注册表；``ctx.escape_monitor`` 插件服务包装本注册表，使「活跃监视器」成为
可替换/可禁用的能力接入点；模块级 ``get_active_monitor`` /
``stop_active_monitor`` 优先经内核服务解析（内核缺失时回退本注册表），保持
既有调用点兼容。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Optional

_logger = logging.getLogger(__name__)

#: 进程级活跃监视器与锁（模块级导出名保持与历史实现兼容）
_active_monitor: Optional[Any] = None
_active_monitor_lock = threading.RLock()


def raw_active_monitor() -> Optional[Any]:
    """直接读取进程级活跃监视器（不探内核）。"""
    with _active_monitor_lock:
        return _active_monitor


def set_active_monitor(monitor: Any) -> None:
    """登记活跃监视器。"""
    global _active_monitor
    with _active_monitor_lock:
        _active_monitor = monitor


def clear_active_monitor(monitor: Any = None) -> None:
    """清除活跃监视器（``monitor=None`` 或与当前实例同一时清除）。"""
    global _active_monitor
    with _active_monitor_lock:
        if monitor is None or _active_monitor is monitor:
            _active_monitor = None


def raw_stop_monitor(monitor: Any = None) -> None:
    """停止指定/当前活跃监视器（不探内核）。"""
    target = monitor if monitor is not None else raw_active_monitor()
    if target is None:
        return
    try:
        target.stop()
    except Exception:
        _logger.warning("EscapeMonitor.stop() 异常", exc_info=True)


def get_active_monitor() -> Optional[Any]:
    """获取活跃 EscapeMonitor（内核 ``ctx.escape_monitor`` 服务优先）。"""
    try:
        from ...kernel.runtime import active_service

        service = active_service("escape_monitor")
        if service is not None:
            return service.active()
    except Exception:  # pragma: no cover - 内核异常时回退
        _logger.debug("内核 escape_monitor 服务解析失败，回退进程级注册表", exc_info=True)
    return raw_active_monitor()


def stop_active_monitor() -> None:
    """停止活跃 EscapeMonitor（内核 ``ctx.escape_monitor`` 服务优先）。"""
    try:
        from ...kernel.runtime import active_service

        service = active_service("escape_monitor")
        if service is not None:
            service.stop()
            return
    except Exception:  # pragma: no cover - 内核异常时回退
        _logger.debug("内核 escape_monitor 服务解析失败，回退进程级注册表", exc_info=True)
    raw_stop_monitor()


__all__ = [
    "raw_active_monitor",
    "set_active_monitor",
    "clear_active_monitor",
    "raw_stop_monitor",
    "get_active_monitor",
    "stop_active_monitor",
    "_active_monitor",
    "_active_monitor_lock",
]
