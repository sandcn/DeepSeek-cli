"""MCP 传输注册表 — 内置传输实现与插件扩展的单一来源。

「一切皆插件」：MCP 传输（``stdio`` / ``http`` / ``sse``）不再由
``src/mcp/transport.py::create_transport`` 的 ``if/elif`` 硬编码分支路由，而是
注册到本注册表；每一项都由清单中的**独立插件条目**（``mcp_transport``）显式
声明，可被 Profile/Bundle 声明，也可被 Patch/Overlay 按 id 单独禁用、覆盖或
替换。

**清单接管**：``mcp`` 聚合插件收到组合根注入的 ``managed_mcp_transports``
（清单已接管的 id，含被禁用的）时经 ``set_managed_builtin_mcp_transports``
声明这些 id 由清单条目负责——对应内置传输不再走默认装配。无清单（单元测试、
独立调用）时无接管，全部内置传输默认生效。

``create_transport(cfg)`` 经 ``resolve_transport(cfg.transport, cfg)`` 解析；
无匹配时回退内置默认（``stdio``），保证未知配置仍可工作。
"""

from __future__ import annotations

import importlib
import logging
import threading
from typing import Any, Callable, Dict, List, Tuple

_logger = logging.getLogger(__name__)

_lock = threading.RLock()
_ABSENT = object()

#: (id, 模块, 类名)
_BUILTIN_SPECS: Tuple[Tuple[str, str, str], ...] = (
    ("stdio", "src.mcp.transport", "StdioTransport"),
    ("http", "src.mcp.transport", "HttpTransport"),
    ("sse", "src.mcp.transport", "SseTransport"),
)


def _make_factory(module_name: str, class_name: str):
    def _factory(cfg):
        module = importlib.import_module(module_name)
        return getattr(module, class_name)(cfg)

    _factory.__name__ = f"_builtin_{class_name}_factory"
    return _factory


_builtin_specs: Dict[str, Callable[[Any], Any]] = {
    spec_id: _make_factory(module_name, class_name)
    for spec_id, module_name, class_name in _BUILTIN_SPECS
}

_registered_builtin: Dict[str, Callable[[Any], Any]] = {}
_managed_builtin: set = set()
_disabled_builtin: set = set()
_extension: Dict[str, Callable[[Any], Any]] = {}

#: 默认传输（未知名称回退）
DEFAULT_TRANSPORT = "stdio"


def _normalize_ids(ids) -> List[str]:
    if isinstance(ids, str):
        ids = [ids]
    selected: List[str] = []
    for item in ids or ():
        if item not in _builtin_specs:
            raise KeyError(f"未知内置 MCP 传输: {item!r}（可用: {list(_builtin_specs)}）")
        selected.append(item)
    return selected


def builtin_mcp_transport_ids() -> list[str]:
    return list(_builtin_specs)


def default_mcp_transport_factory(spec_id: str) -> Callable[[Any], Any]:
    try:
        return _builtin_specs[spec_id]
    except KeyError:
        raise KeyError(f"未知内置 MCP 传输: {spec_id!r}（可用: {list(_builtin_specs)}）") from None


def builtin_mcp_transport_factories() -> Dict[str, Callable[[Any], Any]]:
    with _lock:
        result: Dict[str, Callable[[Any], Any]] = {}
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


def register_builtin_mcp_transport(spec_id: str, factory: Any = None) -> Callable[[], None]:
    if spec_id not in _builtin_specs:
        raise KeyError(f"未知内置 MCP 传输: {spec_id!r}（可用: {list(_builtin_specs)}）")
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


def unregister_builtin_mcp_transport(spec_id: str) -> bool:
    with _lock:
        return _registered_builtin.pop(spec_id, None) is not None


def set_managed_builtin_mcp_transports(ids) -> Callable[[], None]:
    selected = _normalize_ids(ids)
    with _lock:
        added = [item for item in selected if item not in _managed_builtin]
        _managed_builtin.update(added)

    def _undo() -> None:
        with _lock:
            for item in added:
                _managed_builtin.discard(item)

    return _undo


def managed_mcp_transport_ids() -> list[str]:
    with _lock:
        return sorted(_managed_builtin)


def disable_builtin_mcp_transports(ids) -> Callable[[], None]:
    selected = _normalize_ids(ids)
    with _lock:
        added = [item for item in selected if item not in _disabled_builtin]
        _disabled_builtin.update(added)

    def _undo() -> None:
        with _lock:
            for item in added:
                _disabled_builtin.discard(item)

    return _undo


def register_mcp_transport(name: str, factory: Callable[[Any], Any]) -> Callable[[], None]:
    """注册一个扩展传输（``factory(cfg) -> transport``）；返回幂等撤销。"""
    if not isinstance(name, str) or not name:
        raise ValueError(f"传输名必须是非空字符串: {name!r}")
    if not callable(factory):
        raise TypeError(f"传输工厂必须可调用: {factory!r}")
    with _lock:
        previous = _extension.get(name, _ABSENT)
        _extension[name] = factory

    def _undo() -> None:
        with _lock:
            if previous is _ABSENT:
                _extension.pop(name, None)
            else:
                _extension[name] = previous

    return _undo


def mcp_transport_factories() -> Dict[str, Callable[[Any], Any]]:
    with _lock:
        return dict(_extension)


def resolve_transport(name: str, cfg: Any):
    """按名解析并构造传输（扩展 → 生效内置 → 未知回退默认）；无可用时返回 None。"""
    with _lock:
        factory = _extension.get(name)
        if factory is None:
            factory = builtin_mcp_transport_factories().get(name)
        if factory is None and name != DEFAULT_TRANSPORT:
            factory = builtin_mcp_transport_factories().get(DEFAULT_TRANSPORT)
    if factory is None:
        return None
    return factory(cfg)


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
    "DEFAULT_TRANSPORT",
    "builtin_mcp_transport_ids",
    "builtin_mcp_transport_factories",
    "default_mcp_transport_factory",
    "register_builtin_mcp_transport",
    "unregister_builtin_mcp_transport",
    "set_managed_builtin_mcp_transports",
    "managed_mcp_transport_ids",
    "disable_builtin_mcp_transports",
    "register_mcp_transport",
    "mcp_transport_factories",
    "resolve_transport",
    "clear",
    "reset",
]
