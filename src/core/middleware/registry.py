"""Agent 主循环中间件注册表 — 内置中间件与插件扩展的单一来源。

「一切皆插件」：Agent 的 Pipeline 中间件（中断检查 / 可观测性 / 审计）的
**声明**集中在本模块规格表中；每一项都由清单中的**独立插件条目**
（``middleware``）显式注册，因而可被 Profile/Bundle 声明，也可被
Patch/Overlay 按 id 单独禁用、覆盖或替换，而非硬编码在 ``Agent.__init__``
的一次性装配里。

**清单接管**：当 ``agent_middleware`` 聚合插件收到组合根注入的
``managed_middlewares``（清单已接管的 id，含被禁用的）时，经
``set_managed_builtin_middleware`` 声明这些 id 由清单条目负责——对应内置项
不再走默认装配；被禁用（未挂载）的条目因此真正缺席。无清单（单元测试、
独立调用）时无接管，全部内置项默认装配（向后兼容）。

内置中间件与扩展中间件分开存放：``middleware_factories()`` 只返回扩展项，
``builtin_middleware_factories()`` 返回当前生效的内置项。Agent 按「内置 →
扩展」顺序装配（后注册的中间件包裹先注册的）。
"""

from __future__ import annotations

import importlib
import logging
import threading
from typing import Any, Callable, Dict, List, Tuple

_logger = logging.getLogger(__name__)

_ABSENT = object()

#: (id, 模块, 类名)
_BUILTIN_SPECS: Tuple[Tuple[str, str, str], ...] = (
    ("interrupt", "src.core.middleware.interrupt", "_InterruptCheckMiddleware"),
    ("observability", "src.core.middleware.observability", "_AsyncObservabilityMiddleware"),
    ("audit", "src.core.middleware.audit", "_AuditLogMiddleware"),
)

_middlewares: List[Callable[[], Any]] = []
_lock = threading.RLock()


def _make_factory(module_name: str, class_name: str):
    def _factory():
        module = importlib.import_module(module_name)
        return getattr(module, class_name)()

    _factory.__name__ = f"_builtin_{class_name}_factory"
    return _factory


#: id → 默认工厂（声明顺序即装配顺序）
_builtin_specs: Dict[str, Callable[[], Any]] = {
    spec_id: _make_factory(module_name, class_name)
    for spec_id, module_name, class_name in _BUILTIN_SPECS
}

#: 由清单条目注册/覆盖的内置中间件（id → 工厂）
_registered_builtin: Dict[str, Callable[[], Any]] = {}

#: 由清单接管的 id（默认装配被抑制，仅条目注册者生效）
_managed_builtin: set = set()

#: 显式禁用的内置中间件
_disabled_builtin: set = set()


def _normalize_ids(ids) -> List[str]:
    if isinstance(ids, str):
        ids = [ids]
    selected: List[str] = []
    for item in ids or ():
        if item not in _builtin_specs:
            raise KeyError(f"未知内置中间件: {item!r}（可用: {list(_builtin_specs)}）")
        selected.append(item)
    return selected


def builtin_middleware_ids() -> list[str]:
    """全部内置中间件 id（按装配顺序）。"""
    return list(_builtin_specs)


def default_middleware_factory(spec_id: str) -> Callable[[], Any]:
    """返回内置中间件的默认工厂（未知 id 抛 KeyError）。"""
    try:
        return _builtin_specs[spec_id]
    except KeyError:
        raise KeyError(f"未知内置中间件: {spec_id!r}（可用: {list(_builtin_specs)}）") from None


def builtin_middleware_factories() -> tuple:
    """当前生效的内置中间件工厂（快照，按装配顺序）。

    规则：显式禁用 → 跳过；条目注册/覆盖 → 用注册工厂；清单接管且无注册 →
    跳过（由条目负责）；否则 → 默认工厂。
    """
    with _lock:
        result = []
        for spec_id, default in _builtin_specs.items():
            if spec_id in _disabled_builtin:
                continue
            override = _registered_builtin.get(spec_id)
            if override is not None:
                result.append(override)
                continue
            if spec_id in _managed_builtin:
                continue
            result.append(default)
        return tuple(result)


def register_builtin_middleware(spec_id: str, factory: Any = None) -> Callable[[], None]:
    """注册/覆盖一个内置中间件（``factory=None`` 用默认工厂）。

    返回幂等撤销：恢复注册前的状态。
    """
    if spec_id not in _builtin_specs:
        raise KeyError(f"未知内置中间件: {spec_id!r}（可用: {list(_builtin_specs)}）")
    with _lock:
        previous = _registered_builtin.get(spec_id, _ABSENT)
        _registered_builtin[spec_id] = factory if factory is not None else _builtin_specs[spec_id]

    def _undo() -> None:
        with _lock:
            if previous is _ABSENT:
                _registered_builtin.pop(spec_id, None)
            else:
                _registered_builtin[spec_id] = previous

    return _undo


def unregister_builtin_middleware(spec_id: str) -> bool:
    with _lock:
        return _registered_builtin.pop(spec_id, None) is not None


def set_managed_builtin_middleware(ids) -> Callable[[], None]:
    """声明这些内置中间件 id 由清单条目负责（默认装配被抑制）；返回撤销。"""
    selected = _normalize_ids(ids)
    with _lock:
        added = [item for item in selected if item not in _managed_builtin]
        _managed_builtin.update(added)

    def _undo() -> None:
        with _lock:
            for item in added:
                _managed_builtin.discard(item)

    return _undo


def managed_middleware_ids() -> list[str]:
    """当前被清单接管的内置中间件 id（自省用）。"""
    with _lock:
        return sorted(_managed_builtin)


def disable_builtin_middleware(ids) -> Callable[[], None]:
    """禁用一个或多个内置中间件（返回幂等撤销）。"""
    selected = _normalize_ids(ids)
    with _lock:
        added = [item for item in selected if item not in _disabled_builtin]
        _disabled_builtin.update(added)

    def _undo() -> None:
        with _lock:
            for item in added:
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
    """清空扩展中间件与清单注册（测试用；不影响内置默认与禁用状态）。"""
    with _lock:
        _middlewares.clear()
        _registered_builtin.clear()


def reset() -> None:
    """重置全部状态到「无清单、无禁用、全部默认」（测试隔离用）。"""
    with _lock:
        _middlewares.clear()
        _registered_builtin.clear()
        _managed_builtin.clear()
        _disabled_builtin.clear()


__all__ = [
    "builtin_middleware_ids",
    "builtin_middleware_factories",
    "default_middleware_factory",
    "register_builtin_middleware",
    "unregister_builtin_middleware",
    "set_managed_builtin_middleware",
    "managed_middleware_ids",
    "disable_builtin_middleware",
    "register_middleware",
    "middleware_factories",
    "clear",
    "reset",
]
