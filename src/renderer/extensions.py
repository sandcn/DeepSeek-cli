"""渲染器扩展注册表 — 内置 TokenHandler / TokenFilter 与插件扩展的单一来源。

「一切皆插件」：

- **内置 handler / filter**（Inline/Code/Math/...、CodeBlockBatcher/
  HeadingAnchorFilter/TokenStreamOptimizer）的**声明**集中在本模块的规格表中；
  每一项都由清单中的**独立插件条目**（``renderer_handler`` / ``renderer_filter``）
  显式注册，因而可被 Profile/Bundle 声明，也可被 Patch/Overlay 按 id 单独禁用、
  覆盖或替换，而非隐藏在某处的一次性自动注册副作用里。
- **清单接管**：当 ``renderer_builtin`` 聚合插件收到组合根注入的
  ``managed_handlers`` / ``managed_filters``（清单已接管的 id，含被禁用的）时，
  经 ``set_managed_builtin_handlers`` / ``set_managed_builtin_filters`` 声明这
  些 id 由清单条目负责——对应内置项不再走默认装配，只有条目注册的才有；被
  禁用（未挂载）的条目因此真正缺席。无清单（单元测试、独立调用）时无接管，
  全部内置项默认装配（向后兼容）。
- **扩展 handler / filter** 经 ``ctx.renderer.register_handler(...)`` /
  ``register_filter(...)`` 追加；注册是挂在插件 Fiber 上的可逆副作用。

内置项与扩展项分开存放：``handler_factories()`` / ``filter_factories()`` 只
返回**扩展项**（保持既有语义与测试），``builtin_handler_factories()`` /
``builtin_filter_factories()`` 返回当前生效的内置项（清单注册 → 默认 → 跳过
接管/禁用），渲染引擎按「内置 → 扩展」顺序装配（后注册的同 token 类型覆盖先
注册的）。
"""

from __future__ import annotations

import importlib
import logging
import threading
from typing import Any, Callable, Dict, List, Tuple

_logger = logging.getLogger(__name__)

_handlers: List[Callable[[], Any]] = []
_filters: List[Callable[[], Any]] = []
_lock = threading.RLock()

_ABSENT = object()


# ── 内置项声明（id, 模块, 类名, 构造 kwargs） ──────────────

_BUILTIN_HANDLER_SPECS: Tuple[Tuple[str, str, str], ...] = (
    ("inline", "src.renderer.handlers", "InlineHandler"),
    ("code", "src.renderer.handlers", "CodeHandler"),
    ("math", "src.renderer.handlers", "MathHandler"),
    ("mermaid", "src.renderer.handlers", "MermaidHandler"),
    ("details", "src.renderer.handlers", "DetailsHandler"),
    ("admonition", "src.renderer.handlers", "AdmonitionHandler"),
    ("html_block", "src.renderer.handlers", "HtmlBlockHandler"),
    ("table", "src.renderer.handlers", "TableHandler"),
    ("fenced_div", "src.renderer.handlers", "FencedDivHandler"),
)

_BUILTIN_FILTER_SPECS: Tuple[Tuple[str, str, str, dict], ...] = (
    ("code_block_batcher", "src.renderer.pipeline", "CodeBlockBatcher", {}),
    ("heading_anchor", "src.renderer.pipeline_filters.heading_anchor",
     "HeadingAnchorFilter", {"collect_toc": True}),
    ("stream_optimizer", "src.renderer.pipeline_filters.stream_optimizer",
     "TokenStreamOptimizer", {}),
)


def _make_factory(module_name: str, class_name: str, kwargs: dict):
    """构造惰性工厂（延迟导入，避免 renderer 包内的导入环）。"""

    def _factory():
        module = importlib.import_module(module_name)
        cls = getattr(module, class_name)
        return cls(**dict(kwargs))

    _factory.__name__ = f"_builtin_{class_name}_factory"
    return _factory


#: id → 默认工厂（声明顺序即装配顺序）
_builtin_handler_specs: Dict[str, Callable[[], Any]] = {
    spec_id: _make_factory(module_name, class_name, {})
    for spec_id, module_name, class_name in _BUILTIN_HANDLER_SPECS
}
_builtin_filter_specs: Dict[str, Callable[[], Any]] = {
    spec_id: _make_factory(module_name, class_name, kwargs)
    for spec_id, module_name, class_name, kwargs in _BUILTIN_FILTER_SPECS
}

#: 由清单条目注册/覆盖的内置项（id → 工厂）
_registered_builtin_handlers: Dict[str, Callable[[], Any]] = {}
_registered_builtin_filters: Dict[str, Callable[[], Any]] = {}

#: 由清单接管的 id（对应默认装配被抑制，仅条目注册者生效）
_managed_builtin_handlers: set = set()
_managed_builtin_filters: set = set()

#: 显式禁用的内置项
_disabled_builtin_handlers: set = set()
_disabled_builtin_filters: set = set()


# ── id 规格访问 / 校验 ───────────────────────────────────


def _builtin_handler_spec_ids() -> List[str]:
    return list(_builtin_handler_specs)


def _builtin_filter_spec_ids() -> List[str]:
    return list(_builtin_filter_specs)


def _normalize_ids(ids, known: List[str], kind: str) -> List[str]:
    if isinstance(ids, str):
        ids = [ids]
    selected: List[str] = []
    for item in ids or ():
        if item not in known:
            raise KeyError(f"未知内置渲染 {kind}: {item!r}（可用: {known}）")
        selected.append(item)
    return selected


def builtin_handler_ids() -> list[str]:
    """全部内置 handler id（按装配顺序）。"""
    return _builtin_handler_spec_ids()


def builtin_filter_ids() -> list[str]:
    """全部内置 filter id（按装配顺序）。"""
    return _builtin_filter_spec_ids()


def default_handler_factory(spec_id: str) -> Callable[[], Any]:
    """返回内置 handler 的默认工厂（未知 id 抛 KeyError）。"""
    try:
        return _builtin_handler_specs[spec_id]
    except KeyError:
        raise KeyError(f"未知内置渲染 handler: {spec_id!r}（可用: {_builtin_handler_spec_ids()}）") from None


def default_filter_factory(spec_id: str) -> Callable[[], Any]:
    """返回内置 filter 的默认工厂（未知 id 抛 KeyError）。"""
    try:
        return _builtin_filter_specs[spec_id]
    except KeyError:
        raise KeyError(f"未知内置渲染 filter: {spec_id!r}（可用: {_builtin_filter_spec_ids()}）") from None


# ── 内置项装配读取 ───────────────────────────────────────


def builtin_handler_factories() -> tuple:
    """当前生效的内置 handler 工厂（快照，按装配顺序）。

    规则：显式禁用 → 跳过；条目注册/覆盖 → 用注册工厂；清单接管且无注册 →
    跳过（由条目负责）；否则 → 默认工厂。
    """
    with _lock:
        result = []
        for spec_id, default in _builtin_handler_specs.items():
            if spec_id in _disabled_builtin_handlers:
                continue
            override = _registered_builtin_handlers.get(spec_id)
            if override is not None:
                result.append(override)
                continue
            if spec_id in _managed_builtin_handlers:
                continue
            result.append(default)
        return tuple(result)


def builtin_filter_factories() -> tuple:
    """当前生效的内置 filter 工厂（快照，按装配顺序）。"""
    with _lock:
        result = []
        for spec_id, default in _builtin_filter_specs.items():
            if spec_id in _disabled_builtin_filters:
                continue
            override = _registered_builtin_filters.get(spec_id)
            if override is not None:
                result.append(override)
                continue
            if spec_id in _managed_builtin_filters:
                continue
            result.append(default)
        return tuple(result)


# ── 内置项：清单条目注册 / 覆盖 ──────────────────────────


def _register_builtin(
    registry: Dict[str, Callable[[], Any]],
    specs: Dict[str, Callable[[], Any]],
    spec_id: str,
    factory: Any,
    kind: str,
) -> Callable[[], None]:
    if spec_id not in specs:
        raise KeyError(f"未知内置渲染 {kind}: {spec_id!r}（可用: {list(specs)}）")
    with _lock:
        previous = registry.get(spec_id, _ABSENT)
        registry[spec_id] = factory if factory is not None else specs[spec_id]

    def _undo() -> None:
        with _lock:
            if previous is _ABSENT:
                registry.pop(spec_id, None)
            else:
                registry[spec_id] = previous

    return _undo


def register_builtin_handler(spec_id: str, factory: Any = None) -> Callable[[], None]:
    """注册/覆盖一个内置 handler（``factory=None`` 用默认工厂）。

    返回幂等撤销：恢复注册前的状态。
    """
    return _register_builtin(_registered_builtin_handlers, _builtin_handler_specs, spec_id, factory, "handler")


def register_builtin_filter(spec_id: str, factory: Any = None) -> Callable[[], None]:
    """注册/覆盖一个内置 filter（``factory=None`` 用默认工厂）。"""
    return _register_builtin(_registered_builtin_filters, _builtin_filter_specs, spec_id, factory, "filter")


def unregister_builtin_handler(spec_id: str) -> bool:
    with _lock:
        return _registered_builtin_handlers.pop(spec_id, None) is not None


def unregister_builtin_filter(spec_id: str) -> bool:
    with _lock:
        return _registered_builtin_filters.pop(spec_id, None) is not None


# ── 内置项：清单接管声明 ─────────────────────────────────


def _set_managed(managed: set, specs: Dict[str, Callable[[], Any]], ids, kind: str) -> Callable[[], None]:
    selected = _normalize_ids(ids, list(specs), kind)
    with _lock:
        added = [item for item in selected if item not in managed]
        managed.update(added)

    def _undo() -> None:
        with _lock:
            for item in added:
                managed.discard(item)

    return _undo


def set_managed_builtin_handlers(ids) -> Callable[[], None]:
    """声明这些内置 handler id 由清单条目负责（默认装配被抑制）；返回撤销。"""
    return _set_managed(_managed_builtin_handlers, _builtin_handler_specs, ids, "handler")


def set_managed_builtin_filters(ids) -> Callable[[], None]:
    """声明这些内置 filter id 由清单条目负责（默认装配被抑制）；返回撤销。"""
    return _set_managed(_managed_builtin_filters, _builtin_filter_specs, ids, "filter")


def managed_handler_ids() -> list[str]:
    """当前被清单接管的内置 handler id（自省用）。"""
    with _lock:
        return sorted(_managed_builtin_handlers)


def managed_filter_ids() -> list[str]:
    """当前被清单接管的内置 filter id（自省用）。"""
    with _lock:
        return sorted(_managed_builtin_filters)


# ── 内置项：显式禁用 ─────────────────────────────────────


def disable_builtin_handlers(ids) -> Callable[[], None]:
    """禁用一个或多个内置 handler（返回幂等撤销）。"""
    return _disable_builtin(_disabled_builtin_handlers, _builtin_handler_specs, ids, "handler")


def disable_builtin_filters(ids) -> Callable[[], None]:
    """禁用一个或多个内置 filter（返回幂等撤销）。"""
    return _disable_builtin(_disabled_builtin_filters, _builtin_filter_specs, ids, "filter")


def _disable_builtin(disabled: set, specs: Dict[str, Callable[[], Any]], ids, kind: str) -> Callable[[], None]:
    selected = _normalize_ids(ids, list(specs), kind)
    with _lock:
        added = [item for item in selected if item not in disabled]
        disabled.update(added)

    def _undo() -> None:
        with _lock:
            for item in added:
                disabled.discard(item)

    return _undo


# ── 扩展项注册 ───────────────────────────────────────────


def register_handler(factory: Callable[[], Any]) -> Callable[[], None]:
    """注册一个 handler 工厂（返回幂等撤销函数）。"""
    if not callable(factory):
        raise TypeError(f"handler 工厂必须可调用: {factory!r}")
    with _lock:
        _handlers.append(factory)

    released = False

    def _undo() -> None:
        nonlocal released
        if released:
            return
        released = True
        with _lock:
            try:
                _handlers.remove(factory)
            except ValueError:
                pass

    return _undo


def register_filter(factory: Callable[[], Any]) -> Callable[[], None]:
    """注册一个 filter 工厂（返回幂等撤销函数）。"""
    if not callable(factory):
        raise TypeError(f"filter 工厂必须可调用: {factory!r}")
    with _lock:
        _filters.append(factory)

    released = False

    def _undo() -> None:
        nonlocal released
        if released:
            return
        released = True
        with _lock:
            try:
                _filters.remove(factory)
            except ValueError:
                pass

    return _undo


def handler_factories() -> tuple:
    """返回当前注册的扩展 handler 工厂（快照）。"""
    with _lock:
        return tuple(_handlers)


def filter_factories() -> tuple:
    """返回当前注册的扩展 filter 工厂（快照）。"""
    with _lock:
        return tuple(_filters)


def clear() -> None:
    """清空全部扩展项与清单注册（测试用；不影响内置默认与禁用状态）。"""
    with _lock:
        _handlers.clear()
        _filters.clear()
        _registered_builtin_handlers.clear()
        _registered_builtin_filters.clear()


def reset() -> None:
    """重置全部状态到「无清单、无禁用、全部默认」（测试隔离用）。"""
    with _lock:
        _handlers.clear()
        _filters.clear()
        _registered_builtin_handlers.clear()
        _registered_builtin_filters.clear()
        _managed_builtin_handlers.clear()
        _managed_builtin_filters.clear()
        _disabled_builtin_handlers.clear()
        _disabled_builtin_filters.clear()


__all__ = [
    "register_handler",
    "register_filter",
    "handler_factories",
    "filter_factories",
    "builtin_handler_factories",
    "builtin_filter_factories",
    "builtin_handler_ids",
    "builtin_filter_ids",
    "default_handler_factory",
    "default_filter_factory",
    "register_builtin_handler",
    "register_builtin_filter",
    "unregister_builtin_handler",
    "unregister_builtin_filter",
    "set_managed_builtin_handlers",
    "set_managed_builtin_filters",
    "managed_handler_ids",
    "managed_filter_ids",
    "disable_builtin_handlers",
    "disable_builtin_filters",
    "clear",
    "reset",
]
