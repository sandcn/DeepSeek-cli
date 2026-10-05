"""Web 搜索提供者注册表 — 内置搜索提供者与插件扩展的单一来源。

「一切皆插件」：``web_search`` 工具不再硬编码 ``DeepSeekSearchProvider``，
而是经本注册表按名解析提供者；内置 ``deepseek`` 提供者由清单中的独立插件
条目（``web_search_provider``）显式声明，因而可被 Profile/Bundle 声明，也可
被 Patch/Overlay 按 id 单独禁用、覆盖或替换（对齐 dsh 的
``dsh-web-search-deepseek`` 拆包）。

**清单接管**：``web_search`` 聚合插件收到组合根注入的
``managed_search_providers``（清单已接管的 id，含被禁用的）时经
``set_managed_builtin_search_providers`` 声明这些 id 由清单条目负责——对应
内置提供者不再走默认装配。无清单（单元测试、独立调用）时无接管，全部内置
提供者默认生效。

解析优先级：扩展提供者 → 生效内置提供者 → 默认内置（``deepseek``）。
"""

from __future__ import annotations

import importlib
import logging
import threading
from typing import Any, Callable, Dict, List, Tuple

_logger = logging.getLogger(__name__)

_lock = threading.RLock()
_ABSENT = object()

#: (id, 模块, 类名, 显示标签)
_BUILTIN_SPECS: Tuple[Tuple[str, str, str, str], ...] = (
    ("deepseek", "src.tools.search_providers", "DeepSeekSearchProvider", "DeepSeek"),
)


def _make_factory(module_name: str, class_name: str):
    def _factory():
        module = importlib.import_module(module_name)
        return getattr(module, class_name)()

    _factory.__name__ = f"_builtin_{class_name}_factory"
    return _factory


_builtin_specs: Dict[str, Callable[[], Any]] = {
    spec_id: _make_factory(module_name, class_name)
    for spec_id, module_name, class_name, _ in _BUILTIN_SPECS
}
_builtin_labels: Dict[str, str] = {
    spec_id: label for spec_id, _, _, label in _BUILTIN_SPECS
}

_registered_builtin: Dict[str, Callable[[], Any]] = {}
_managed_builtin: set = set()
_disabled_builtin: set = set()
_extension: Dict[str, Callable[[], Any]] = {}

#: 默认搜索提供者（未知名称/未指定时使用）
DEFAULT_SEARCH_PROVIDER = "deepseek"


def _normalize_ids(ids) -> List[str]:
    if isinstance(ids, str):
        ids = [ids]
    selected: List[str] = []
    for item in ids or ():
        if item not in _builtin_specs:
            raise KeyError(f"未知内置搜索提供者: {item!r}（可用: {list(_builtin_specs)}）")
        selected.append(item)
    return selected


def builtin_search_provider_ids() -> list[str]:
    return list(_builtin_specs)


def builtin_search_provider_labels() -> Dict[str, str]:
    return dict(_builtin_labels)


def default_search_provider_factory(spec_id: str) -> Callable[[], Any]:
    try:
        return _builtin_specs[spec_id]
    except KeyError:
        raise KeyError(f"未知内置搜索提供者: {spec_id!r}（可用: {list(_builtin_specs)}）") from None


def builtin_search_provider_factories() -> Dict[str, Callable[[], Any]]:
    """当前生效的内置提供者工厂（``id → 工厂``）。

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


def register_builtin_search_provider(spec_id: str, factory: Any = None) -> Callable[[], None]:
    """注册/覆盖一个内置提供者（``factory=None`` 用默认工厂）；返回幂等撤销。"""
    if spec_id not in _builtin_specs:
        raise KeyError(f"未知内置搜索提供者: {spec_id!r}（可用: {list(_builtin_specs)}）")
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


def unregister_builtin_search_provider(spec_id: str) -> bool:
    with _lock:
        return _registered_builtin.pop(spec_id, None) is not None


def set_managed_builtin_search_providers(ids) -> Callable[[], None]:
    selected = _normalize_ids(ids)
    with _lock:
        added = [item for item in selected if item not in _managed_builtin]
        _managed_builtin.update(added)

    def _undo() -> None:
        with _lock:
            for item in added:
                _managed_builtin.discard(item)

    return _undo


def managed_search_provider_ids() -> list[str]:
    with _lock:
        return sorted(_managed_builtin)


def disable_builtin_search_providers(ids) -> Callable[[], None]:
    selected = _normalize_ids(ids)
    with _lock:
        added = [item for item in selected if item not in _disabled_builtin]
        _disabled_builtin.update(added)

    def _undo() -> None:
        with _lock:
            for item in added:
                _disabled_builtin.discard(item)

    return _undo


def register_search_provider(name: str, factory: Callable[[], Any]) -> Callable[[], None]:
    """注册一个扩展搜索提供者（``factory() -> provider``）；返回幂等撤销。"""
    if not isinstance(name, str) or not name:
        raise ValueError(f"提供者名必须是非空字符串: {name!r}")
    if not callable(factory):
        raise TypeError(f"提供者工厂必须可调用: {factory!r}")
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


def search_provider_factories() -> Dict[str, Callable[[], Any]]:
    with _lock:
        return dict(_extension)


def resolve_search_provider(name: str = "") -> Any:
    """按名解析搜索提供者实例（扩展 → 生效内置 → 未知回退默认）；无可用返回 None。"""
    with _lock:
        spec_id = name or DEFAULT_SEARCH_PROVIDER
        factory = _extension.get(spec_id)
        if factory is None:
            factory = builtin_search_provider_factories().get(spec_id)
        if factory is None and spec_id != DEFAULT_SEARCH_PROVIDER:
            factory = builtin_search_provider_factories().get(DEFAULT_SEARCH_PROVIDER)
    if factory is None:
        return None
    return factory()


def active_search_provider(name: str = "") -> Any:
    """解析搜索提供者（内核 ``ctx.web_search`` 优先，回退进程级注册表）。

    内核挂载 ``ctx.web_search`` 服务后返回其解析的提供者实例；内核缺失或
    服务尚在构造中时回退本模块的进程级注册表。
    """
    try:
        from ..kernel.runtime import active_service

        service = active_service("web_search")
        if service is not None:
            provider = service.resolve_provider(name or None)
            if provider is not None:
                return provider
    except Exception:  # pragma: no cover - 内核异常时回退
        _logger.debug("内核 web_search 服务解析失败，回退进程级注册表", exc_info=True)
    return resolve_search_provider(name)


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
    "DEFAULT_SEARCH_PROVIDER",
    "builtin_search_provider_ids",
    "builtin_search_provider_labels",
    "builtin_search_provider_factories",
    "default_search_provider_factory",
    "register_builtin_search_provider",
    "unregister_builtin_search_provider",
    "set_managed_builtin_search_providers",
    "managed_search_provider_ids",
    "disable_builtin_search_providers",
    "register_search_provider",
    "search_provider_factories",
    "resolve_search_provider",
    "active_search_provider",
    "clear",
    "reset",
]
