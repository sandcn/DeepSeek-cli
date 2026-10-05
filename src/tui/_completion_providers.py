"""补全提供者注册表 — 终端补全提供者的单一来源（一切皆插件）。

「一切皆插件」：``CompletionEngine`` 的补全提供者（command / path / param）不再
硬编码在 ``complete`` 的分支里，而是注册到本模块的规格表；每个提供者由清单中
的**独立插件条目**（``completion_provider``）显式注册，因而可被 Profile/Bundle
声明，也可被 Patch/Overlay 按 id 单独禁用或替换（整提供者实现替换）。

**清单接管**：``completion_providers`` 聚合插件收到组合根注入的
``managed_completion_providers``（清单已接管的 id，含被禁用的）时经
``set_managed_builtin_providers`` 声明这些 id 由清单条目负责——对应内置项不再
走默认装配；被禁用（未挂载）的条目因此真正缺席。无清单（单元测试、独立调用）
时无接管，全部内置项默认生效。

提供者实现签名为 ``handler(engine, ctx) -> list[CompletionItem]``，``ctx`` 为
``CompletionContext``（text / words / last_word）。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Dict, List, Tuple

_lock = threading.RLock()
_ABSENT = object()


@dataclass(frozen=True)
class CompletionProvider:
    """一个补全提供者规格。"""

    id: str
    order: int
    handler: str

    def to_dict(self) -> dict:
        return {"id": self.id, "order": self.order, "handler": self.handler}


#: 内置提供者声明（order 决定解析顺序）
_BUILTIN_SPECS: Tuple[CompletionProvider, ...] = (
    CompletionProvider("command", 0, "src.tui._completion_engine:_command_provider"),
    CompletionProvider("param", 1, "src.tui._completion_engine:_param_provider"),
    CompletionProvider("path", 2, "src.tui._completion_engine:_path_provider"),
)

_builtin_specs: Dict[str, CompletionProvider] = {spec.id: spec for spec in _BUILTIN_SPECS}

_registered_builtin: Dict[str, CompletionProvider] = {}
_managed_builtin: set = set()
_disabled_builtin: set = set()
_extension: Dict[str, CompletionProvider] = {}


def _import_attr(dotted: str):
    module_name, _, attr = dotted.partition(":")
    import importlib

    return getattr(importlib.import_module(module_name), attr)


def _normalize_ids(ids) -> List[str]:
    if isinstance(ids, str):
        ids = [ids]
    selected: List[str] = []
    for item in ids or ():
        if item not in _builtin_specs:
            raise KeyError(f"未知内置补全提供者: {item!r}（可用: {list(_builtin_specs)}）")
        selected.append(item)
    return selected


def builtin_provider_ids() -> list[str]:
    return list(_builtin_specs)


def default_provider(spec_id: str) -> CompletionProvider:
    try:
        return _builtin_specs[spec_id]
    except KeyError:
        raise KeyError(f"未知内置补全提供者: {spec_id!r}（可用: {list(_builtin_specs)}）") from None


def active_providers() -> Dict[str, CompletionProvider]:
    with _lock:
        result: Dict[str, CompletionProvider] = {}
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


def active_provider_ids() -> list[str]:
    providers = active_providers()
    return sorted(providers, key=lambda pid: providers[pid].order)


def register_builtin_provider(spec_id: str, spec: CompletionProvider = None) -> Callable[[], None]:
    if spec_id not in _builtin_specs:
        raise KeyError(f"未知内置补全提供者: {spec_id!r}（可用: {list(_builtin_specs)}）")
    with _lock:
        previous = _registered_builtin.get(spec_id, _ABSENT)
        _registered_builtin[spec_id] = spec if spec is not None else _builtin_specs[spec_id]

    def _undo() -> None:
        with _lock:
            if previous is _ABSENT:
                _registered_builtin.pop(spec_id, None)
            else:
                _registered_builtin[spec_id] = previous

    return _undo


def unregister_builtin_provider(spec_id: str) -> bool:
    with _lock:
        return _registered_builtin.pop(spec_id, None) is not None


def set_managed_builtin_providers(ids) -> Callable[[], None]:
    selected = _normalize_ids(ids)
    with _lock:
        added = [item for item in selected if item not in _managed_builtin]
        _managed_builtin.update(added)

    def _undo() -> None:
        with _lock:
            for item in added:
                _managed_builtin.discard(item)

    return _undo


def managed_provider_ids() -> list[str]:
    with _lock:
        return sorted(_managed_builtin)


def disable_builtin_providers(ids) -> Callable[[], None]:
    selected = _normalize_ids(ids)
    with _lock:
        added = [item for item in selected if item not in _disabled_builtin]
        _disabled_builtin.update(added)

    def _undo() -> None:
        with _lock:
            for item in added:
                _disabled_builtin.discard(item)

    return _undo


def register_provider(spec: CompletionProvider) -> Callable[[], None]:
    if not isinstance(spec, CompletionProvider):
        raise TypeError(f"扩展提供者必须是 CompletionProvider: {spec!r}")
    with _lock:
        previous = _extension.get(spec.id, _ABSENT)
        _extension[spec.id] = spec

    def _undo() -> None:
        with _lock:
            if previous is _ABSENT:
                _extension.pop(spec.id, None)
            else:
                _extension[spec.id] = previous

    return _undo


def unregister_provider(spec_id: str) -> bool:
    with _lock:
        return _extension.pop(spec_id, None) is not None


def extension_providers() -> Dict[str, CompletionProvider]:
    with _lock:
        return dict(_extension)


def resolve_provider(spec_id: str) -> Callable:
    """解析某提供者的实现（扩展优先 → 生效内置 → None）。"""
    with _lock:
        spec = _extension.get(spec_id)
        if spec is None:
            spec = active_providers().get(spec_id)
    if spec is None:
        return None
    return _import_attr(spec.handler)


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
    "CompletionProvider",
    "builtin_provider_ids",
    "default_provider",
    "active_providers",
    "active_provider_ids",
    "register_builtin_provider",
    "unregister_builtin_provider",
    "set_managed_builtin_providers",
    "managed_provider_ids",
    "disable_builtin_providers",
    "register_provider",
    "unregister_provider",
    "extension_providers",
    "resolve_provider",
    "clear",
    "reset",
]
