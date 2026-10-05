"""渲染目标注册表 — 内置目标实现与插件扩展的单一来源。

「一切皆插件」：渲染目标（``terminal`` / ``file``）不再是仅有的抽象基类，
而是注册到本注册表；每一项都由清单中的**独立插件条目**
（``renderer_target``，经 ``src.plugins.renderer_targets``）显式声明，因而可
被 Profile/Bundle 声明，也可被 Patch/Overlay 按 id 单独禁用、覆盖或替换。

**清单接管**：``renderer`` 聚合插件收到组合根注入的
``managed_render_targets``（清单已接管的 id，含被禁用的）时经
``set_managed_builtin_render_targets`` 声明这些 id 由清单条目负责——对应内置
目标不再走默认装配。无清单（单元测试、独立调用）时无接管，全部内置目标默认
生效。

解析优先级：扩展目标 → 生效内置目标 → 未知回退默认（``terminal``）。
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
    ("terminal", "src.renderer.targets.terminal", "TerminalRenderTarget"),
    ("file", "src.renderer.targets.file", "FileRenderTarget"),
)


def _make_factory(module_name: str, class_name: str):
    def _factory(**kwargs):
        module = importlib.import_module(module_name)
        return getattr(module, class_name)(**kwargs)

    _factory.__name__ = f"_builtin_{class_name}_factory"
    return _factory


_builtin_specs: Dict[str, Callable[..., Any]] = {
    spec_id: _make_factory(module_name, class_name)
    for spec_id, module_name, class_name in _BUILTIN_SPECS
}

_registered_builtin: Dict[str, Callable[..., Any]] = {}
_managed_builtin: set = set()
_disabled_builtin: set = set()
_extension: Dict[str, Callable[..., Any]] = {}

#: 默认渲染目标（未知名称回退）
DEFAULT_RENDER_TARGET = "terminal"


def _normalize_ids(ids) -> List[str]:
    if isinstance(ids, str):
        ids = [ids]
    selected: List[str] = []
    for item in ids or ():
        if item not in _builtin_specs:
            raise KeyError(f"未知内置渲染目标: {item!r}（可用: {list(_builtin_specs)}）")
        selected.append(item)
    return selected


def builtin_render_target_ids() -> list[str]:
    return list(_builtin_specs)


def default_render_target_factory(spec_id: str) -> Callable[..., Any]:
    try:
        return _builtin_specs[spec_id]
    except KeyError:
        raise KeyError(f"未知内置渲染目标: {spec_id!r}（可用: {list(_builtin_specs)}）") from None


def builtin_render_target_factories() -> Dict[str, Callable[..., Any]]:
    """当前生效的内置目标工厂（``id → 工厂``）。"""
    with _lock:
        result: Dict[str, Callable[..., Any]] = {}
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


def register_builtin_render_target(spec_id: str, factory: Any = None) -> Callable[[], None]:
    """注册/覆盖一个内置目标（``factory=None`` 用默认工厂）；返回幂等撤销。"""
    if spec_id not in _builtin_specs:
        raise KeyError(f"未知内置渲染目标: {spec_id!r}（可用: {list(_builtin_specs)}）")
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


def unregister_builtin_render_target(spec_id: str) -> bool:
    with _lock:
        return _registered_builtin.pop(spec_id, None) is not None


def set_managed_builtin_render_targets(ids) -> Callable[[], None]:
    selected = _normalize_ids(ids)
    with _lock:
        added = [item for item in selected if item not in _managed_builtin]
        _managed_builtin.update(added)

    def _undo() -> None:
        with _lock:
            for item in added:
                _managed_builtin.discard(item)

    return _undo


def managed_render_target_ids() -> list[str]:
    with _lock:
        return sorted(_managed_builtin)


def disable_builtin_render_targets(ids) -> Callable[[], None]:
    selected = _normalize_ids(ids)
    with _lock:
        added = [item for item in selected if item not in _disabled_builtin]
        _disabled_builtin.update(added)

    def _undo() -> None:
        with _lock:
            for item in added:
                _disabled_builtin.discard(item)

    return _undo


def register_render_target(name: str, factory: Callable[..., Any]) -> Callable[[], None]:
    """注册一个扩展渲染目标（``factory(**kwargs) -> RenderTarget``）；返回幂等撤销。"""
    if not isinstance(name, str) or not name:
        raise ValueError(f"目标名必须是非空字符串: {name!r}")
    if not callable(factory):
        raise TypeError(f"目标工厂必须可调用: {factory!r}")
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


def render_target_factories() -> Dict[str, Callable[..., Any]]:
    with _lock:
        return dict(_extension)


def resolve_render_target(name: str = "", **kwargs):
    """按名构造渲染目标（扩展 → 生效内置 → 未知回退默认）；无可用返回 None。"""
    with _lock:
        spec_id = name or DEFAULT_RENDER_TARGET
        factory = _extension.get(spec_id)
        if factory is None:
            factory = builtin_render_target_factories().get(spec_id)
        if factory is None and spec_id != DEFAULT_RENDER_TARGET:
            factory = builtin_render_target_factories().get(DEFAULT_RENDER_TARGET)
    if factory is None:
        return None
    return factory(**kwargs)


def active_render_target(name: str = "", **kwargs):
    """构造渲染目标（内核 ``ctx.renderer`` 服务优先，回退进程级注册表）。"""
    try:
        from ...kernel.runtime import active_service

        service = active_service("renderer")
        if service is not None:
            target = service.create_target(name or None, **kwargs)
            if target is not None:
                return target
    except Exception:  # pragma: no cover - 内核异常时回退
        _logger.debug("内核 renderer 服务解析失败，回退进程级注册表", exc_info=True)
    return resolve_render_target(name, **kwargs)


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
    "DEFAULT_RENDER_TARGET",
    "builtin_render_target_ids",
    "builtin_render_target_factories",
    "default_render_target_factory",
    "register_builtin_render_target",
    "unregister_builtin_render_target",
    "set_managed_builtin_render_targets",
    "managed_render_target_ids",
    "disable_builtin_render_targets",
    "register_render_target",
    "render_target_factories",
    "resolve_render_target",
    "active_render_target",
    "clear",
    "reset",
]
