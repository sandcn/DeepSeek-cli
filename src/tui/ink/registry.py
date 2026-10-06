"""host 组件注册表 — 自定义 host 标签 + 内置 host 条目（一切皆插件）。

布局与绘制泛化：应用可注册 ``(tag, measure_fn, paint_fn)``：
  - measure_fn(fiber, avail_w) -> (width, height)：测量容器/叶子尺寸。
  - paint_fn(fiber, canvas)：将内容绘制到画布（canvas 为 {col: (char, style)}）。

由 layout._measure / components._paint 在标准 host 标签（box/text/static/
spacer/app）之外查询本注册表。

「一切皆插件」：内置 host（``static-lines``）的 measure/paint 不再是
``staticlines`` 模块导入期的一次性副作用，而是注册到本模块的规格表；由清单
中的**独立插件条目**（``host``）显式注册，因而可被 Profile/Bundle 声明，也可
被 Patch/Overlay 按 id 单独禁用或替换（`hosts` 聚合插件经 ``managed_hosts``
抑制默认装配）。

★ P3（review）：注册表为全局可变对象，被 ``_measure``/``_paint`` 热路径查询
——运行期被任意模块 ``register_host`` 覆盖即静默改变渲染行为。现对「同 tag
重复注册且实现不同」记 warning（幂等覆盖仍允许——测试重注册场景需要），
使语义漂移可观测。
"""

from __future__ import annotations

import importlib
import logging
import threading
from typing import Callable, Dict, List, Optional, Tuple

_logger = logging.getLogger(__name__)

_lock = threading.RLock()
_ABSENT = object()

#: (tag, measure 点分引用, paint 点分引用)
_BUILTIN_SPECS: Tuple[Tuple[str, str, str], ...] = (
    ("static-lines", "src.tui.ink.widgets.staticlines:_measure", "src.tui.ink.widgets.staticlines:_paint"),
)

_builtin_specs: Dict[str, Tuple[str, str]] = {
    tag: (measure_ref, paint_ref) for tag, measure_ref, paint_ref in _BUILTIN_SPECS
}

#: 由清单条目注册/覆盖的内置 host（tag → (measure, paint)）
_registered_builtin: Dict[str, Tuple[Callable, Callable]] = {}
#: 由清单接管的内置 host tag（默认装配被抑制）
_managed_builtin: set = set()
#: 显式禁用的内置 host tag
_disabled_builtin: set = set()
#: 扩展 host（tag → (measure, paint)）
_REGISTRY: Dict[str, Tuple[Callable, Callable]] = {}
#: ``active_hosts()`` 结果缓存（tag → (measure, paint)）。热路径每帧多次查询，
#: 修复前每次重建 dict 并对每个内置 host 做 ``importlib.import_module``
#: （实测每帧 ~200 次模块查找）；注册/禁用/接管状态变化时失效。
_ACTIVE_HOSTS_CACHE: Optional[Dict[str, Tuple[Callable, Callable]]] = None


def _invalidate_hosts_cache() -> None:
    global _ACTIVE_HOSTS_CACHE
    _ACTIVE_HOSTS_CACHE = None


def _set_active_hosts_cache(value) -> None:
    global _ACTIVE_HOSTS_CACHE
    _ACTIVE_HOSTS_CACHE = value


def _import_attr(dotted: str):
    module_name, _, attr = dotted.partition(":")
    return getattr(importlib.import_module(module_name), attr)


def _normalize_ids(tags) -> List[str]:
    if isinstance(tags, str):
        tags = [tags]
    selected: List[str] = []
    for item in tags or ():
        if item not in _builtin_specs:
            raise KeyError(f"未知内置 host: {item!r}（可用: {list(_builtin_specs)}）")
        selected.append(item)
    return selected


def builtin_host_ids() -> list[str]:
    """全部内置 host tag（按声明顺序）。"""
    return list(_builtin_specs)


def default_host(tag: str) -> Tuple[Callable, Callable]:
    try:
        measure_ref, paint_ref = _builtin_specs[tag]
    except KeyError:
        raise KeyError(f"未知内置 host: {tag!r}（可用: {list(_builtin_specs)}）") from None
    return (_import_attr(measure_ref), _import_attr(paint_ref))


def active_hosts() -> Dict[str, Tuple[Callable, Callable]]:
    """当前生效的内置 host（``tag → (measure, paint)``）。

    ★ 性能：结果缓存，注册/禁用/接管状态变化时失效（``_invalidate_hosts_cache``）
    ——修复前每次调用都重建 dict 并对每个内置 host 做 ``importlib`` 模块解析
    （渲染热路径每帧调用上百次，实测每帧 ~200 次模块查找）。
    """
    cached = _ACTIVE_HOSTS_CACHE
    if cached is not None:
        return cached
    with _lock:
        if _ACTIVE_HOSTS_CACHE is not None:
            return _ACTIVE_HOSTS_CACHE
        result: Dict[str, Tuple[Callable, Callable]] = {}
        for tag, refs in _builtin_specs.items():
            if tag in _disabled_builtin:
                continue
            override = _registered_builtin.get(tag)
            if override is not None:
                result[tag] = override
                continue
            if tag in _managed_builtin:
                continue
            result[tag] = (_import_attr(refs[0]), _import_attr(refs[1]))
        _set_active_hosts_cache(result)
        return result


def register_builtin_host(tag: str, pair: Optional[Tuple[Callable, Callable]] = None) -> Callable[[], None]:
    """注册/覆盖一个内置 host（``pair=None`` 用默认实现）；返回幂等撤销。"""
    if tag not in _builtin_specs:
        raise KeyError(f"未知内置 host: {tag!r}（可用: {list(_builtin_specs)}）")
    with _lock:
        previous = _registered_builtin.get(tag, _ABSENT)
        _registered_builtin[tag] = pair if pair is not None else default_host(tag)
        _invalidate_hosts_cache()

    def _undo() -> None:
        with _lock:
            if previous is _ABSENT:
                _registered_builtin.pop(tag, None)
            else:
                _registered_builtin[tag] = previous
            _invalidate_hosts_cache()

    return _undo


def unregister_builtin_host(tag: str) -> bool:
    with _lock:
        removed = _registered_builtin.pop(tag, None) is not None
        if removed:
            _invalidate_hosts_cache()
        return removed


def set_managed_builtin_hosts(ids) -> Callable[[], None]:
    """声明这些内置 host tag 由清单条目负责（默认装配被抑制）；返回撤销。"""
    selected = _normalize_ids(ids)
    with _lock:
        added = [item for item in selected if item not in _managed_builtin]
        _managed_builtin.update(added)
        _invalidate_hosts_cache()

    def _undo() -> None:
        with _lock:
            for item in added:
                _managed_builtin.discard(item)
            _invalidate_hosts_cache()

    return _undo


def managed_host_ids() -> list[str]:
    with _lock:
        return sorted(_managed_builtin)


def disable_builtin_hosts(ids) -> Callable[[], None]:
    """禁用一个或多个内置 host（返回幂等撤销）。"""
    selected = _normalize_ids(ids)
    with _lock:
        added = [item for item in selected if item not in _disabled_builtin]
        _disabled_builtin.update(added)
        _invalidate_hosts_cache()

    def _undo() -> None:
        with _lock:
            for item in added:
                _disabled_builtin.discard(item)
            _invalidate_hosts_cache()

    return _undo


def register_host(tag: str, measure_fn: Callable, paint_fn: Callable) -> None:
    """注册自定义 host 组件（同 tag 重复注册覆盖，实现变化时告警）。

    Args:
        tag: host 标签名。
        measure_fn: ``(fiber, avail_w) -> (width, height)``。
        paint_fn: ``(fiber, canvas)``。
    """
    with _lock:
        existing = _REGISTRY.get(tag)
        if existing is not None and existing != (measure_fn, paint_fn):
            _logger.warning("register_host 覆盖已注册 host %s（实现不相同的重注册）", tag)
        _REGISTRY[tag] = (measure_fn, paint_fn)
        _invalidate_hosts_cache()


def unregister_host(tag: str) -> None:
    """注销自定义 host（测试用）。"""
    with _lock:
        _REGISTRY.pop(tag, None)
        _invalidate_hosts_cache()


def get_host(tag: str) -> Optional[Tuple[Callable, Callable]]:
    """查询 host 组件（扩展优先 → 生效内置；无匹配返回 None）。"""
    with _lock:
        host = _REGISTRY.get(tag)
        if host is not None:
            return host
    return active_hosts().get(tag)


def has_host(tag: str) -> bool:
    return get_host(tag) is not None


def clear() -> None:
    """清空扩展项与清单注册（测试用；不影响内置默认与禁用状态）。"""
    with _lock:
        _REGISTRY.clear()
        _registered_builtin.clear()
        _invalidate_hosts_cache()


def reset() -> None:
    """重置全部状态到「无清单、无禁用、全部默认」（测试隔离用）。"""
    with _lock:
        _REGISTRY.clear()
        _registered_builtin.clear()
        _managed_builtin.clear()
        _disabled_builtin.clear()
        _invalidate_hosts_cache()


__all__ = [
    "builtin_host_ids",
    "default_host",
    "active_hosts",
    "register_builtin_host",
    "unregister_builtin_host",
    "set_managed_builtin_hosts",
    "managed_host_ids",
    "disable_builtin_hosts",
    "register_host",
    "unregister_host",
    "get_host",
    "has_host",
    "clear",
    "reset",
]
