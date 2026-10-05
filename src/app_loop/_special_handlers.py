"""特殊键处理器注册表 — action → 处理器工厂的单一来源（一切皆插件）。

「一切皆插件」：``make_special_key_callback`` 的 action 分发（vim / editmsg /
retry / toggle_theme / switch_model / cycle_mode）不再是硬编码的 if/elif 链，
而是注册到本模块；每个内置处理器由清单中的**独立插件条目**（``special_key``）
显式注册，因而可被 Profile/Bundle 声明，也可被 Patch/Overlay 按 id 单独禁用、
覆盖或替换。

**清单接管**：``special_keys`` 聚合插件收到组合根注入的
``managed_special_keys``（清单已接管的 id，含被禁用的）时经
``set_managed_builtin_special_keys`` 声明这些 id 由清单条目负责——对应内置
项不再走默认装配；被禁用（未挂载）的条目因此真正缺席。无清单（单元测试、
独立调用）时无接管，全部内置项默认生效。

处理器工厂签名为 ``builder(env) -> Callable[[str], str | None]``，``env`` 为
``make_special_key_callback`` 传入的运行时上下文（loop/session/state/chat_ui/
monitor）。内置工厂声明为点分引用（``模块:函数``），调用时惰性导入——保证对
``src.app_loop._special_keys`` 模块属性的 monkeypatch 仍然生效。
"""

from __future__ import annotations

import threading
from typing import Callable, Dict, List, Tuple

_lock = threading.RLock()
_ABSENT = object()

#: (id, 处理器工厂点分引用)
_BUILTIN_SPECS: Tuple[Tuple[str, str], ...] = (
    ("vim", "src.app_loop._special_keys:make_vim_handler"),
    ("editmsg", "src.app_loop._special_keys:make_editmsg_handler"),
    ("retry", "src.app_loop._special_keys:make_retry_handler"),
    ("toggle_theme", "src.app_loop._special_keys:make_toggle_theme_handler"),
    ("switch_model", "src.app_loop._special_keys:make_switch_model_handler"),
    ("cycle_mode", "src.app_loop._special_keys:make_cycle_mode_handler"),
)

#: 旧 action 名 → 规范 id（向后兼容别名）
_ALIASES: Dict[str, str] = {"empty_mode": "cycle_mode"}

_builtin_specs: Dict[str, str] = {spec_id: ref for spec_id, ref in _BUILTIN_SPECS}

_registered_builtin: Dict[str, Callable] = {}
_managed_builtin: set = set()
_disabled_builtin: set = set()
_extension: Dict[str, Callable] = {}


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
            raise KeyError(f"未知内置特殊键处理器: {item!r}（可用: {list(_builtin_specs)}）")
        selected.append(item)
    return selected


def builtin_special_key_ids() -> list[str]:
    """全部内置特殊键处理器 id（按声明顺序）。"""
    return list(_builtin_specs)


def default_special_key_factory(spec_id: str) -> Callable:
    try:
        return _import_attr(_builtin_specs[spec_id])
    except KeyError:
        raise KeyError(f"未知内置特殊键处理器: {spec_id!r}（可用: {list(_builtin_specs)}）") from None


def active_special_key_factories() -> Dict[str, Callable]:
    """当前生效的内置处理器工厂（``id → builder``；按声明顺序）。"""
    with _lock:
        result: Dict[str, Callable] = {}
        for spec_id, ref in _builtin_specs.items():
            if spec_id in _disabled_builtin:
                continue
            override = _registered_builtin.get(spec_id)
            if override is not None:
                result[spec_id] = override
                continue
            if spec_id in _managed_builtin:
                continue
            result[spec_id] = _import_attr(ref)
        return result


def register_builtin_special_key(spec_id: str, factory: Callable = None) -> Callable[[], None]:
    """注册/覆盖一个内置处理器（``factory=None`` 用默认工厂）；返回幂等撤销。"""
    if spec_id not in _builtin_specs:
        raise KeyError(f"未知内置特殊键处理器: {spec_id!r}（可用: {list(_builtin_specs)}）")
    with _lock:
        previous = _registered_builtin.get(spec_id, _ABSENT)
        _registered_builtin[spec_id] = factory if factory is not None else _import_attr(_builtin_specs[spec_id])

    def _undo() -> None:
        with _lock:
            if previous is _ABSENT:
                _registered_builtin.pop(spec_id, None)
            else:
                _registered_builtin[spec_id] = previous

    return _undo


def unregister_builtin_special_key(spec_id: str) -> bool:
    with _lock:
        return _registered_builtin.pop(spec_id, None) is not None


def set_managed_builtin_special_keys(ids) -> Callable[[], None]:
    """声明这些内置 id 由清单条目负责（默认装配被抑制）；返回撤销。"""
    selected = _normalize_ids(ids)
    with _lock:
        added = [item for item in selected if item not in _managed_builtin]
        _managed_builtin.update(added)

    def _undo() -> None:
        with _lock:
            for item in added:
                _managed_builtin.discard(item)

    return _undo


def managed_special_key_ids() -> list[str]:
    with _lock:
        return sorted(_managed_builtin)


def disable_builtin_special_keys(ids) -> Callable[[], None]:
    """禁用一个或多个内置处理器（返回幂等撤销）。"""
    selected = _normalize_ids(ids)
    with _lock:
        added = [item for item in selected if item not in _disabled_builtin]
        _disabled_builtin.update(added)

    def _undo() -> None:
        with _lock:
            for item in added:
                _disabled_builtin.discard(item)

    return _undo


def register_special_key(spec_id: str, factory: Callable) -> Callable[[], None]:
    """注册一个扩展处理器（id 覆盖内置 / 新 action）；返回幂等撤销。"""
    if not isinstance(spec_id, str) or not spec_id:
        raise ValueError(f"处理器 id 必须是非空字符串: {spec_id!r}")
    if not callable(factory):
        raise TypeError(f"处理器工厂必须可调用: {factory!r}")
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


def unregister_special_key(spec_id: str) -> bool:
    with _lock:
        return _extension.pop(spec_id, None) is not None


def extension_special_keys() -> Dict[str, Callable]:
    with _lock:
        return dict(_extension)


def resolve_special_key_factory(action: str) -> Callable:
    """解析某 action 的处理器工厂（扩展优先 → 生效内置 → None）。"""
    real = _ALIASES.get(action, action)
    with _lock:
        factory = _extension.get(real)
        if factory is not None:
            return factory
        return active_special_key_factories().get(real)


def build_special_key_handlers(env) -> Dict[str, Callable]:
    """为给定 env 构造 ``action → handler`` 映射（扩展 + 生效内置 + 别名）。"""
    handlers: Dict[str, Callable] = {}
    for action, factory in extension_special_keys().items():
        handlers[action] = factory(env)
    for action, factory in active_special_key_factories().items():
        handlers.setdefault(action, factory(env))
    for alias, real in _ALIASES.items():
        if real in handlers:
            handlers.setdefault(alias, handlers[real])
    return handlers


def clear() -> None:
    """清空扩展项与清单注册（测试用；不影响内置默认与禁用状态）。"""
    with _lock:
        _extension.clear()
        _registered_builtin.clear()


def reset() -> None:
    """重置全部状态到「无清单、无禁用、全部默认」（测试隔离用）。"""
    with _lock:
        _extension.clear()
        _registered_builtin.clear()
        _managed_builtin.clear()
        _disabled_builtin.clear()


__all__ = [
    "builtin_special_key_ids",
    "default_special_key_factory",
    "active_special_key_factories",
    "register_builtin_special_key",
    "unregister_builtin_special_key",
    "set_managed_builtin_special_keys",
    "managed_special_key_ids",
    "disable_builtin_special_keys",
    "register_special_key",
    "unregister_special_key",
    "extension_special_keys",
    "resolve_special_key_factory",
    "build_special_key_handlers",
    "clear",
    "reset",
]
