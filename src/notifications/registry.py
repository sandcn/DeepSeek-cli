"""通知后端注册表 — 平台通知后端与插件扩展的单一来源。

「一切皆插件」：桌面通知的三种平台后端（Termux / Linux notify-send /
Windows Toast）的**声明**集中在本模块规格表中；每一项都由清单中的**独立
插件条目**（``notification_backend``）显式注册，因而可被 Profile/Bundle
声明，也可被 Patch/Overlay 按 id 单独禁用、覆盖或替换。

**清单接管**：``notifications`` 聚合插件收到组合根注入的
``managed_notification_backends``（清单已接管的 id，含被禁用的）时经
``set_managed_builtin_notification_backends`` 声明这些 id 由清单条目负责——
对应内置后端不再走默认装配；被禁用（未挂载）的条目因此真正缺席。无清单
（单元测试、独立调用）时无接管，全部内置后端默认装配。

``src.notifications.notify_chat_completed`` / ``async_notify_chat_completed``
按本注册表的**生效后端**扇出发送；后端只需实现 ``send(preview, title)`` 与
可选 ``asend(preview, title)``。
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import threading
from typing import Any, Callable, Dict, List, Tuple

_logger = logging.getLogger(__name__)

_lock = threading.RLock()
_ABSENT = object()

#: (id, 模块, 类名)
_BUILTIN_SPECS: Tuple[Tuple[str, str, str], ...] = (
    ("termux", "src.notifications.backends", "TermuxBackend"),
    ("linux", "src.notifications.backends", "LinuxBackend"),
    ("windows", "src.notifications.backends", "WindowsBackend"),
)


def _make_factory(module_name: str, class_name: str):
    def _factory():
        module = importlib.import_module(module_name)
        return getattr(module, class_name)()

    _factory.__name__ = f"_builtin_{class_name}_factory"
    return _factory


_builtin_specs: Dict[str, Callable[[], Any]] = {
    spec_id: _make_factory(module_name, class_name)
    for spec_id, module_name, class_name in _BUILTIN_SPECS
}

_registered_builtin: Dict[str, Callable[[], Any]] = {}
_managed_builtin: set = set()
_disabled_builtin: set = set()
_extension: Dict[str, Callable[[], Any]] = {}


def _normalize_ids(ids) -> List[str]:
    if isinstance(ids, str):
        ids = [ids]
    selected: List[str] = []
    for item in ids or ():
        if item not in _builtin_specs:
            raise KeyError(f"未知内置通知后端: {item!r}（可用: {list(_builtin_specs)}）")
        selected.append(item)
    return selected


def builtin_notification_backend_ids() -> list[str]:
    return list(_builtin_specs)


def default_notification_backend_factory(spec_id: str) -> Callable[[], Any]:
    try:
        return _builtin_specs[spec_id]
    except KeyError:
        raise KeyError(f"未知内置通知后端: {spec_id!r}（可用: {list(_builtin_specs)}）") from None


def builtin_notification_backend_factories() -> Dict[str, Callable[[], Any]]:
    """当前生效的内置后端工厂（``id → 工厂``）。

    规则：显式禁用 → 跳过；条目注册/覆盖 → 用注册工厂；清单接管且无注册 →
    跳过；否则 → 默认工厂。
    """
    with _lock:
        result: Dict[str, Callable[[], Any]] = {}
        for spec_id, default in _builtin_specs.items():
            if spec_id in _disabled_builtin:
                continue
            override = _registered_builtin.get(spec_id)
            if override is not None:
                result[spec_id] = override
                continue
            if spec_id in _managed_builtin:
                continue
            result[spec_id] = default
        return result


def register_builtin_notification_backend(spec_id: str, factory: Any = None) -> Callable[[], None]:
    """注册/覆盖一个内置后端（``factory=None`` 用默认工厂）；返回幂等撤销。"""
    if spec_id not in _builtin_specs:
        raise KeyError(f"未知内置通知后端: {spec_id!r}（可用: {list(_builtin_specs)}）")
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


def unregister_builtin_notification_backend(spec_id: str) -> bool:
    with _lock:
        return _registered_builtin.pop(spec_id, None) is not None


def set_managed_builtin_notification_backends(ids) -> Callable[[], None]:
    selected = _normalize_ids(ids)
    with _lock:
        added = [item for item in selected if item not in _managed_builtin]
        _managed_builtin.update(added)

    def _undo() -> None:
        with _lock:
            for item in added:
                _managed_builtin.discard(item)

    return _undo


def managed_notification_backend_ids() -> list[str]:
    with _lock:
        return sorted(_managed_builtin)


def disable_builtin_notification_backends(ids) -> Callable[[], None]:
    selected = _normalize_ids(ids)
    with _lock:
        added = [item for item in selected if item not in _disabled_builtin]
        _disabled_builtin.update(added)

    def _undo() -> None:
        with _lock:
            for item in added:
                _disabled_builtin.discard(item)

    return _undo


def register_notification_backend(spec_id: str, factory: Callable[[], Any]) -> Callable[[], None]:
    """注册一个扩展后端（新后端参与扇出）；返回幂等撤销。"""
    if not isinstance(spec_id, str) or not spec_id:
        raise ValueError(f"后端 id 必须是非空字符串: {spec_id!r}")
    if not callable(factory):
        raise TypeError(f"后端工厂必须可调用: {factory!r}")
    with _lock:
        previous = _extension.get(spec_id, _ABSENT)
        _extension[spec_id] = factory

    def _undo() -> None:
        with _lock:
            if previous is _ABSENT:
                _extension.pop(spec_id, None)
            else:
                _extension[spec_id] = previous

    return _undo


def notification_backend_factories() -> Dict[str, Callable[[], Any]]:
    with _lock:
        return dict(_extension)


def active_notification_backends() -> List[Any]:
    """实例化当前生效的通知后端（内置 + 扩展），跳过构造失败项。"""
    with _lock:
        factories = dict(builtin_notification_backend_factories())
        factories.update(_extension)
    backends: List[Any] = []
    for spec_id, factory in factories.items():
        try:
            backends.append(factory())
        except Exception:
            _logger.warning("通知后端构造失败: %s", spec_id, exc_info=True)
    return backends


def clear() -> None:
    with _lock:
        _extension.clear()
        _registered_builtin.clear()


def reset() -> None:
    with _lock:
        _extension.clear()
        _registered_builtin.clear()
        _managed_builtin.clear()
        _disabled_builtin.clear()


__all__ = [
    "builtin_notification_backend_ids",
    "builtin_notification_backend_factories",
    "default_notification_backend_factory",
    "register_builtin_notification_backend",
    "unregister_builtin_notification_backend",
    "set_managed_builtin_notification_backends",
    "managed_notification_backend_ids",
    "disable_builtin_notification_backends",
    "register_notification_backend",
    "notification_backend_factories",
    "active_notification_backends",
    "clear",
    "reset",
]
