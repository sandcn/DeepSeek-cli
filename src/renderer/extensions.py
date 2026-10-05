"""渲染器扩展注册表 — 内置 TokenHandler / TokenFilter 与插件扩展的单一来源。

「一切皆插件」：

- **内置 handler / filter**（Inline/Code/Math/...、CodeBlockBatcher/
  HeadingAnchorFilter/TokenStreamOptimizer）不再硬编码在 ``RenderEngine`` /
  ``IncrementalRenderer`` 的构造里，而是登记在本模块的**内置注册表**中；渲染
  引擎只从注册表装配。内置项可经 ``disable_builtin_handlers`` /
  ``disable_builtin_filters``（由 ``renderer_builtin`` 插件按清单配置调用）
  整体禁用或替换。
- **扩展 handler / filter** 经 ``ctx.renderer.register_handler(...)`` /
  ``register_filter(...)`` 追加；注册是挂在插件 Fiber 上的可逆副作用。

内置项与扩展项分开存放：``handler_factories()`` / ``filter_factories()`` 只
返回**扩展项**（保持既有语义与测试），``builtin_handler_factories()`` /
``builtin_filter_factories()`` 返回未禁用的内置项，渲染引擎按「内置 → 扩展」
顺序装配（后注册的同 token 类型覆盖先注册的）。
"""

from __future__ import annotations

import importlib
import logging
import threading
from typing import Any, Callable, List, Tuple

_logger = logging.getLogger(__name__)

_handlers: List[Callable[[], Any]] = []
_filters: List[Callable[[], Any]] = []
_lock = threading.RLock()


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

_disabled_builtin_handlers: set[str] = set()
_disabled_builtin_filters: set[str] = set()


def _make_factory(module_name: str, class_name: str, kwargs: dict):
    """构造惰性工厂（延迟导入，避免 renderer 包内的导入环）。"""

    def _factory():
        module = importlib.import_module(module_name)
        cls = getattr(module, class_name)
        return cls(**dict(kwargs))

    _factory.__name__ = f"_builtin_{class_name}_factory"
    return _factory


_builtin_handler_factories: List[Tuple[str, Callable[[], Any]]] = [
    (spec_id, _make_factory(module_name, class_name, {}))
    for spec_id, module_name, class_name in _BUILTIN_HANDLER_SPECS
]
_builtin_filter_factories: List[Tuple[str, Callable[[], Any]]] = [
    (spec_id, _make_factory(module_name, class_name, kwargs))
    for spec_id, module_name, class_name, kwargs in _BUILTIN_FILTER_SPECS
]


# ── 内置项访问 / 禁用 ────────────────────────────────────


def builtin_handler_ids() -> list[str]:
    """全部内置 handler id（按装配顺序）。"""
    return [spec_id for spec_id, _ in _builtin_handler_factories]


def builtin_filter_ids() -> list[str]:
    """全部内置 filter id（按装配顺序）。"""
    return [spec_id for spec_id, _ in _builtin_filter_factories]


def builtin_handler_factories() -> tuple:
    """未禁用的内置 handler 工厂（快照，按装配顺序）。"""
    with _lock:
        return tuple(
            factory for spec_id, factory in _builtin_handler_factories
            if spec_id not in _disabled_builtin_handlers
        )


def builtin_filter_factories() -> tuple:
    """未禁用的内置 filter 工厂（快照，按装配顺序）。"""
    with _lock:
        return tuple(
            factory for spec_id, factory in _builtin_filter_factories
            if spec_id not in _disabled_builtin_filters
        )


def disable_builtin_handlers(ids) -> Callable[[], None]:
    """禁用一个或多个内置 handler（返回幂等撤销）。"""
    return _disable_builtin(_disabled_builtin_handlers, builtin_handler_ids(), ids, "handler")


def disable_builtin_filters(ids) -> Callable[[], None]:
    """禁用一个或多个内置 filter（返回幂等撤销）。"""
    return _disable_builtin(_disabled_builtin_filters, builtin_filter_ids(), ids, "filter")


def _disable_builtin(disabled: set, known: list, ids, kind: str) -> Callable[[], None]:
    if isinstance(ids, str):
        ids = [ids]
    selected = []
    for item in ids or ():
        if item not in known:
            raise KeyError(f"未知内置渲染 {kind}: {item!r}（可用: {known}）")
        if item not in disabled:
            disabled.add(item)
            selected.append(item)

    def _undo() -> None:
        with _lock:
            for item in selected:
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
    """清空全部扩展项（测试用；不影响内置项）。"""
    with _lock:
        _handlers.clear()
        _filters.clear()


__all__ = [
    "register_handler",
    "register_filter",
    "handler_factories",
    "filter_factories",
    "builtin_handler_factories",
    "builtin_filter_factories",
    "builtin_handler_ids",
    "builtin_filter_ids",
    "disable_builtin_handlers",
    "disable_builtin_filters",
    "clear",
]
