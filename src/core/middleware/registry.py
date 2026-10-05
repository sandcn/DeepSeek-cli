"""Agent 主循环中间件注册表 — 内置中间件与插件扩展的单一来源。

「一切皆插件」：Agent 的 Pipeline 中间件（中断检查 / 可观测性 / 审计）不再
硬编码在 ``Agent.__init__``，而是登记在本模块的**内置注册表**里；Agent 只从
注册表装配。内置项可经 ``disable_builtin_middleware``（由 ``agent_middleware``
插件按清单配置调用）禁用或替换；插件也可经 ``register_middleware`` 追加自定义
中间件（注册即副作用，随 Fiber 卸载撤销）。

内置中间件与扩展中间件分开存放：``middleware_factories()`` 只返回扩展项，
``builtin_middleware_factories()`` 返回未禁用的内置项。Agent 按「内置 → 扩展」
顺序装配（后注册的中间件包裹先注册的）。
"""

from __future__ import annotations

import importlib
import logging
import threading
from typing import Any, Callable, List, Tuple

_logger = logging.getLogger(__name__)

#: (id, 模块, 类名)
_BUILTIN_SPECS: Tuple[Tuple[str, str, str], ...] = (
    ("interrupt", "src.core.middleware.interrupt", "_InterruptCheckMiddleware"),
    ("observability", "src.core.middleware.observability", "_AsyncObservabilityMiddleware"),
    ("audit", "src.core.middleware.audit", "_AuditLogMiddleware"),
)

_middlewares: List[Callable[[], Any]] = []
_disabled_builtin: set[str] = set()
_lock = threading.RLock()


def _make_factory(module_name: str, class_name: str):
    def _factory():
        module = importlib.import_module(module_name)
        return getattr(module, class_name)()

    _factory.__name__ = f"_builtin_{class_name}_factory"
    return _factory


_builtin_factories: List[Tuple[str, Callable[[], Any]]] = [
    (spec_id, _make_factory(module_name, class_name))
    for spec_id, module_name, class_name in _BUILTIN_SPECS
]


def builtin_middleware_ids() -> list[str]:
    """全部内置中间件 id（按装配顺序）。"""
    return [spec_id for spec_id, _ in _builtin_factories]


def builtin_middleware_factories() -> tuple:
    """未禁用的内置中间件工厂（快照，按装配顺序）。"""
    with _lock:
        return tuple(
            factory for spec_id, factory in _builtin_factories
            if spec_id not in _disabled_builtin
        )


def disable_builtin_middleware(ids) -> Callable[[], None]:
    """禁用一个或多个内置中间件（返回幂等撤销）。"""
    if isinstance(ids, str):
        ids = [ids]
    known = builtin_middleware_ids()
    selected = []
    with _lock:
        for item in ids or ():
            if item not in known:
                raise KeyError(f"未知内置中间件: {item!r}（可用: {known}）")
            if item not in _disabled_builtin:
                _disabled_builtin.add(item)
                selected.append(item)

    def _undo() -> None:
        with _lock:
            for item in selected:
                _disabled_builtin.discard(item)

    return _undo


def register_middleware(factory: Callable[[], Any]) -> Callable[[], None]:
    """注册一个中间件工厂（返回幂等撤销函数）。"""
    if not callable(factory):
        raise TypeError(f"中间件工厂必须可调用: {factory!r}")
    with _lock:
        _middlewares.append(factory)

    released = False

    def _undo() -> None:
        nonlocal released
        if released:
            return
        released = True
        with _lock:
            try:
                _middlewares.remove(factory)
            except ValueError:
                pass

    return _undo


def middleware_factories() -> tuple:
    """返回当前注册的扩展中间件工厂（快照）。"""
    with _lock:
        return tuple(_middlewares)


def clear() -> None:
    """清空扩展中间件（测试用；不影响内置项）。"""
    with _lock:
        _middlewares.clear()


__all__ = [
    "builtin_middleware_ids",
    "builtin_middleware_factories",
    "disable_builtin_middleware",
    "register_middleware",
    "middleware_factories",
    "clear",
]
