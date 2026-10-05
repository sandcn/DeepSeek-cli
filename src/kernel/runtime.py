"""内核运行时访问点 — 进程级内核服务解析（零业务依赖）。

「一切皆插件」：基础设施层/领域层需要「当前内核提供的实现」时，经本模块
依赖倒置访问——内核存在且提供该 key 时返回内核服务，否则返回默认值，由
调用方回退既有默认实现（保证单元测试与独立调用兼容）。

**严格模式（生产路径）**：组合根（``src/plugins/bootstrap.build_kernel``）在
内核稳定后按清单声明激活严格模式——凡由 Profile 插件树 ``provide`` 声明的
服务，运行期缺失即抛 :class:`ServiceUnavailable`，不再静默回退到直接 import /
进程级单例。无内核（单元测试、独立调用）或未激活严格模式时保持既有回退语义。

本模块只依赖内核自身，不 import 任何业务模块；因此基础设施层（如
``src.skills`` / ``src.tools`` / ``src.core.commands``）可以安全地在模块顶层
引用它，而不会引入循环依赖。
"""

from __future__ import annotations

from typing import Any, Iterable, FrozenSet


class ServiceUnavailable(RuntimeError):
    """严格模式下必需服务缺失。"""

    def __init__(self, key: str, profile: str = "") -> None:
        suffix = f"（profile={profile}）" if profile else ""
        super().__init__(
            f"内核必需服务 {key!r} 缺失{suffix}：插件树声明了该服务但运行期未提供"
            "（严格模式下不再回退直接 import / 进程级单例）"
        )
        self.key = key


_strict: bool = False
_required: FrozenSet[str] = frozenset()
_strict_kernel: Any = None


def activate_strict(required_keys: Iterable[str] = (), kernel: Any = None) -> None:
    """激活严格模式（组合根调用），登记必需服务 key 集合与所属内核。"""
    global _strict, _required, _strict_kernel
    _strict = True
    _required = frozenset(str(key) for key in required_keys if key)
    _strict_kernel = kernel


def deactivate_strict() -> None:
    """关闭严格模式（内核卸载时调用），恢复回退语义。"""
    global _strict, _required, _strict_kernel
    _strict = False
    _required = frozenset()
    _strict_kernel = None


def strict_enabled() -> bool:
    return _strict


def strict_required() -> FrozenSet[str]:
    return _required


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
    """按 key 解析内核服务（缺失时返回 default）。

    严格模式下，若 ``key`` 属于本 Profile 声明的必需服务且缺失，抛
    :class:`ServiceUnavailable`（生产路径显式失败，不再静默回退）。
    """
    kernel = current_kernel()
    if kernel is None:
        return default
    from .context import _MISSING

    value = kernel.resolve_service(key)
    if value is _MISSING:
        if _strict and _strict_kernel is kernel and key in _required:
            raise ServiceUnavailable(key, getattr(kernel, "profile", "") or "")
        return default
    return value


def require_service(key: str) -> Any:
    """解析内核服务；严格模式下必需服务缺失抛 :class:`ServiceUnavailable`。

    非严格模式 / 无内核时缺失返回 ``None``（调用方自行处理）。
    """
    kernel = current_kernel()
    if kernel is None:
        return None
    from .context import _MISSING

    value = kernel.resolve_service(key)
    if value is _MISSING:
        if _strict and _strict_kernel is kernel and key in _required:
            raise ServiceUnavailable(key, getattr(kernel, "profile", "") or "")
        return None
    return value


__all__ = [
    "ServiceUnavailable",
    "activate_strict",
    "deactivate_strict",
    "strict_enabled",
    "strict_required",
    "current_kernel",
    "has_kernel",
    "has_service",
    "active_service",
    "require_service",
]
