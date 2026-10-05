"""流式处理器注册表 — 内置流式 chunk 处理器与插件扩展的单一来源。

「一切皆插件」：流式管线（``AsyncStreamPipeline``）的四个内置处理器
（``reasoning`` / ``content`` / ``tool_calls`` / ``speed``）的**声明**集中在
本模块规格表中；每一项都由清单中的**独立插件条目**（``stream_handler``）显式
注册，因而可被 Profile/Bundle 声明，也可被 Patch/Overlay 按 id 单独禁用、覆盖
或替换，而非硬编码在 ``AsyncStreamPipeline.__init__`` 的一次性装配里。

**清单接管**：``stream`` 聚合插件收到组合根注入的 ``managed_stream_handlers``
（清单已接管的 id，含被禁用的）时经 ``set_managed_builtin_stream_handlers``
声明这些 id 由清单条目负责——对应内置项不再走默认装配；被禁用（未挂载）的
条目因此真正缺席。无清单（单元测试、独立调用）时无接管，全部内置项默认装配。

内置项与扩展项分开存放：``stream_handler_factories()`` 只返回扩展项，
``builtin_stream_handler_factories()`` 返回当前生效的内置项。管线按
「内置 → 扩展」顺序解析各角色处理器；某角色缺席时该处理器为 ``None``，
调用方按需降级（不阻断流）。
"""

from __future__ import annotations

import importlib
import threading
from typing import Any, Callable, Dict, List, Tuple

_lock = threading.RLock()
_ABSENT = object()

#: (id, 模块, 类名)
_BUILTIN_SPECS: Tuple[Tuple[str, str, str], ...] = (
    ("reasoning", "src.api.stream.handlers.reasoning", "ReasoningHandler"),
    ("content", "src.api.stream.handlers.content", "ContentHandler"),
    ("tool_calls", "src.api.stream.handlers.tool_calls", "ToolCallsHandler"),
    ("speed", "src.api.stream.handlers.speed", "SpeedHandler"),
)


def _make_factory(module_name: str, class_name: str):
    def _factory():
        module = importlib.import_module(module_name)
        return getattr(module, class_name)()

    _factory.__name__ = f"_builtin_{class_name}_factory"
    return _factory


#: id → 默认工厂（声明顺序即角色顺序）
_builtin_specs: Dict[str, Callable[[], Any]] = {
    spec_id: _make_factory(module_name, class_name)
    for spec_id, module_name, class_name in _BUILTIN_SPECS
}

#: 由清单条目注册/覆盖的内置项（id → 工厂）
_registered_builtin: Dict[str, Callable[[], Any]] = {}
#: 由清单接管的内置项 id（默认装配被抑制）
_managed_builtin: set = set()
#: 显式禁用的内置项
_disabled_builtin: set = set()
#: 扩展处理器（id → 工厂；同 id 覆盖内置解析结果，新 id 为附加角色）
_extension: Dict[str, Callable[[], Any]] = {}


def _normalize_ids(ids) -> List[str]:
    if isinstance(ids, str):
        ids = [ids]
    selected: List[str] = []
    for item in ids or ():
        if item not in _builtin_specs:
            raise KeyError(f"未知内置流式处理器: {item!r}（可用: {list(_builtin_specs)}）")
        selected.append(item)
    return selected


def builtin_stream_handler_ids() -> list[str]:
    """全部内置流式处理器 id（按角色顺序）。"""
    return list(_builtin_specs)


def default_stream_handler_factory(spec_id: str) -> Callable[[], Any]:
    try:
        return _builtin_specs[spec_id]
    except KeyError:
        raise KeyError(f"未知内置流式处理器: {spec_id!r}（可用: {list(_builtin_specs)}）") from None


def builtin_stream_handler_factories() -> Dict[str, Callable[[], Any]]:
    """当前生效的内置处理器工厂（``id → 工厂``）。

    规则：显式禁用 → 跳过；条目注册/覆盖 → 用注册工厂；清单接管且无注册 →
    跳过（由条目负责）；否则 → 默认工厂。
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


def register_builtin_stream_handler(spec_id: str, factory: Any = None) -> Callable[[], None]:
    """注册/覆盖一个内置处理器（``factory=None`` 用默认工厂）；返回幂等撤销。"""
    if spec_id not in _builtin_specs:
        raise KeyError(f"未知内置流式处理器: {spec_id!r}（可用: {list(_builtin_specs)}）")
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


def unregister_builtin_stream_handler(spec_id: str) -> bool:
    with _lock:
        return _registered_builtin.pop(spec_id, None) is not None


def set_managed_builtin_stream_handlers(ids) -> Callable[[], None]:
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


def managed_stream_handler_ids() -> list[str]:
    with _lock:
        return sorted(_managed_builtin)


def disable_builtin_stream_handlers(ids) -> Callable[[], None]:
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


def register_stream_handler(spec_id: str, factory: Callable[[], Any]) -> Callable[[], None]:
    """注册一个扩展处理器（同 id 覆盖内置解析结果）；返回幂等撤销。"""
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


def stream_handler_factories() -> Dict[str, Callable[[], Any]]:
    """返回当前注册的扩展处理器工厂（快照）。"""
    with _lock:
        return dict(_extension)


def resolve_stream_handler(spec_id: str, *args: Any, **kwargs: Any) -> Any:
    """实例化某角色的当前处理器（扩展优先 → 生效内置 → 缺席返回 None）。"""
    with _lock:
        factory = _extension.get(spec_id)
        if factory is None:
            factory = builtin_stream_handler_factories().get(spec_id)
    if factory is None:
        return None
    return factory(*args, **kwargs)


def null_stream_handler(spec_id: str) -> Any:
    """构造某角色的「空处理器」——保留核心累积/状态，禁用副作用（事件/统计）。

    显式禁用（``disable_builtin_stream_handlers``）或清单接管但未挂载的处理器
    缺席时，管线用空处理器保证流不中断：``reasoning`` / ``content`` 仍累积正文
    与 token（结果正确），仅不再发布分块事件（无流式渲染）；``speed`` 跳过统计
    更新；``tool_calls`` 跳过工具调用解析（显式禁用的预期后果）。
    """
    if spec_id not in _builtin_specs:
        raise KeyError(f"未知内置流式处理器: {spec_id!r}（可用: {list(_builtin_specs)}）")
    if spec_id == "reasoning":
        from .handlers.reasoning import ReasoningHandler

        class _NullReasoning(ReasoningHandler):
            def buffer(self, text, label=None):
                return None

            def flush(self, label=None):
                return None

        return _NullReasoning()
    if spec_id == "content":
        from .handlers.content import ContentHandler

        class _NullContent(ContentHandler):
            def buffer(self, text, label=None):
                return None

            def flush(self, label=None):
                return None

        return _NullContent()
    if spec_id == "speed":
        from .handlers.speed import SpeedHandler

        class _NullSpeed(SpeedHandler):
            def try_update(self, ctx):
                return None

            def final_update(self, ctx):
                return None

        return _NullSpeed()
    from .handlers.tool_calls import ToolCallsHandler

    class _NullToolCalls(ToolCallsHandler):
        async def handle(self, ctx, dtc):
            return None

    return _NullToolCalls()


def resolved_stream_handler(spec_id: str) -> Any:
    """解析某角色处理器；缺席时回退空处理器（保证管线不中断）。"""
    handler = resolve_stream_handler(spec_id)
    return handler if handler is not None else null_stream_handler(spec_id)


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
    "builtin_stream_handler_ids",
    "builtin_stream_handler_factories",
    "default_stream_handler_factory",
    "register_builtin_stream_handler",
    "unregister_builtin_stream_handler",
    "set_managed_builtin_stream_handlers",
    "managed_stream_handler_ids",
    "disable_builtin_stream_handlers",
    "register_stream_handler",
    "stream_handler_factories",
    "resolve_stream_handler",
    "resolved_stream_handler",
    "null_stream_handler",
    "clear",
    "reset",
]
