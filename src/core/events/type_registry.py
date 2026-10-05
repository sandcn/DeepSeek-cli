"""事件类型注册表 — 每个内置事件类型一条独立声明（一切皆插件）。

「一切皆插件」：事件类型（核心事件类型字符串常量 + 显示事件类型 dataclass）
不再是各模块的硬编码字面量/类元组，而是注册到本模块的声明表；每个事件类型由
清单中的**独立插件条目**（``event_type``，经
``src.plugins.event_type_entries``）注册，因而可被 Profile/Bundle 声明，也可被
Patch/Overlay 按 id 单独禁用、覆盖（替换值/类）或替换。

事件类型以 ``<domain>::<name>`` 复合 id 登记（域：``core`` = 核心事件类型字符串，
``display`` = 显示事件类型类）。

**清单接管**：``event_types`` 聚合插件收到组合根注入的 ``managed_event_types``
（清单已接管的复合 id，含被禁用的）时经 ``set_managed_builtin_events`` 声明这些
id 由清单条目负责——对应内置声明不再走默认装配；被禁用（未挂载）的条目因此
真正缺席（``event_value`` 返回调用方默认值 / 常量属性抛 AttributeError）。无清单
（单元测试、独立调用）时无接管，全部内置声明默认生效。

本模块为叶子模块（仅依赖 ``src.declarative``），供核心层与表现层消费。
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List

from ...declarative import DeclarativeRegistry

_SEPARATOR = "::"

_REGISTRY = DeclarativeRegistry("事件类型")

_gen_lock = threading.RLock()
_generation = 0


def generation() -> int:
    with _gen_lock:
        return _generation


def _bump() -> None:
    global _generation
    with _gen_lock:
        _generation += 1


def _wrapped(undo: Callable[[], None]) -> Callable[[], None]:
    def _undo() -> None:
        undo()
        _bump()

    return _undo


def composite_id(domain: str, name: str) -> str:
    return f"{domain}{_SEPARATOR}{name}"


def split_id(spec_id: str) -> tuple:
    domain, _, name = str(spec_id).partition(_SEPARATOR)
    return domain, name


def declare_events(domain: str, specs) -> None:
    """登记某域的内置事件类型声明（``{名称: 值}``；首次声明为准）。"""
    items = specs.items() if isinstance(specs, dict) else specs
    _REGISTRY.declare({composite_id(domain, str(name)): value for name, value in items})
    _bump()


def builtin_event_ids(domain: str) -> List[str]:
    """某域全部内置事件类型名（含被接管/禁用，按声明顺序）。"""
    prefix = f"{domain}{_SEPARATOR}"
    return [name for name in _REGISTRY.builtin_ids() if name.startswith(prefix)]


def builtin_composite_ids() -> List[str]:
    return list(_REGISTRY.builtin_ids())


def default_event(domain: str, name: str) -> Any:
    return _REGISTRY.default(composite_id(domain, name))


def active_events(domain: str) -> Dict[str, Any]:
    """某域当前生效的事件类型（``名称 → 值``）。"""
    prefix = f"{domain}{_SEPARATOR}"
    return {
        key[len(prefix):]: value
        for key, value in _REGISTRY.active().items()
        if key.startswith(prefix)
    }


def active_event_ids(domain: str) -> List[str]:
    return list(active_events(domain))


def event_value(domain: str, name: str, default: Any = None) -> Any:
    """按域/名称取当前生效值（缺席返回 ``default``）。"""
    return active_events(domain).get(name, default)


def registered_composite_ids() -> List[str]:
    return list(_REGISTRY.active())


def event_by_composite(spec_id: str, default: Any = None) -> Any:
    """按复合 id 取当前生效值（缺席返回 ``default``）。"""
    return _REGISTRY.active().get(spec_id, default)


# ── 注册 / 撤销 ───────────────────────────────────────


def register_builtin_event(spec_id: str, value: Any = None) -> Callable[[], None]:
    """注册/覆盖内置事件类型（``value=None`` 用默认声明）；返回幂等撤销。"""
    undo = _REGISTRY.register_builtin(spec_id, value)
    _bump()
    return _wrapped(undo)


def unregister_builtin_event(spec_id: str) -> bool:
    if _REGISTRY.unregister_builtin(spec_id):
        _bump()
        return True
    return False


def register_event(spec_id: str, value: Any) -> Callable[[], None]:
    """注册扩展事件类型（不受内置约束）；返回幂等撤销。"""
    undo = _REGISTRY.register_extension(spec_id, value)
    _bump()
    return _wrapped(undo)


def unregister_event(spec_id: str) -> bool:
    if _REGISTRY.unregister_extension(spec_id):
        _bump()
        return True
    return False


# ── 清单接管 / 禁用 ───────────────────────────────────


def set_managed_builtin_events(ids) -> Callable[[], None]:
    undo = _REGISTRY.set_managed(ids)
    _bump()
    return _wrapped(undo)


def managed_event_ids() -> List[str]:
    return list(_REGISTRY.managed_ids())


def disable_builtin_events(ids) -> Callable[[], None]:
    undo = _REGISTRY.disable_builtin(ids)
    _bump()
    return _wrapped(undo)


def describe() -> List[dict]:
    return _REGISTRY.describe()


def clear() -> None:
    _REGISTRY.clear()
    _bump()


def reset() -> None:
    _REGISTRY.reset()
    _bump()


__all__ = [
    "generation",
    "composite_id",
    "split_id",
    "declare_events",
    "builtin_event_ids",
    "builtin_composite_ids",
    "default_event",
    "active_events",
    "active_event_ids",
    "event_value",
    "registered_composite_ids",
    "event_by_composite",
    "register_builtin_event",
    "unregister_builtin_event",
    "register_event",
    "unregister_event",
    "set_managed_builtin_events",
    "managed_event_ids",
    "disable_builtin_events",
    "describe",
    "clear",
    "reset",
]
