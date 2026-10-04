"""内核运行时访问点 — 进程级内核服务解析（零业务依赖）。

「一切皆插件」：基础设施层/领域层需要「当前内核提供的实现」时，经本模块
依赖倒置访问——内核存在且提供该 key 时返回内核服务，否则返回默认值，由
调用方回退既有默认实现（保证单元测试与独立调用兼容）。

本模块只依赖内核自身，不 import 任何业务模块；因此基础设施层（如
``src.skills`` / ``src.tools`` / ``src.core.commands``）可以安全地在模块顶层
引用它，而不会引入循环依赖。
"""

from __future__ import annotations

from typing import Any


def current_kernel() -> Any:
    """返回进程级当前内核（无内核时返回 None）。"""
    try:
        from .kernel import get_current_kernel

        return get_current_kernel()
    except Exception:  # pragma: no cover - 极端导入失败兜底
        return None


def has_kernel() -> bool:
    """当前进程是否已挂载内核插件树。"""
    return current_kernel() is not None


def has_service(key: str) -> bool:
    """内核是否已提供指定服务。"""
    kernel = current_kernel()
    if kernel is None:
        return False
    return kernel.has_service(key)


def active_service(key: str, default: Any = None) -> Any:
    """按 key 解析内核服务（缺失时返回 default）。"""
    kernel = current_kernel()
    if kernel is None:
        return default
    from .context import _MISSING

    value = kernel.resolve_service(key)
    if value is _MISSING:
        return default
    return value


__all__ = ["current_kernel", "has_kernel", "has_service", "active_service"]
