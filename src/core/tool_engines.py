"""工具执行引擎注册表 — 内置执行策略与插件扩展的单一来源。

「一切皆插件」：工具批次的执行策略（``dag`` / ``serial`` / ``parallel``）不再
硬编码在 ``ToolScheduler.schedule`` 里，而是注册到本注册表；每一项都由清单中的
**独立插件条目**（``tool_engine``）显式声明，可被 Profile/Bundle 声明，也可被
Patch/Overlay 按 id 单独禁用、覆盖或替换。

引擎签名::

    async def engine(scheduler, tool_calls, *, agent_ref,
                     on_before, on_after, run_method, is_outermost) -> results

- ``dag``（默认）：全局 DAG 拓扑分层并发调度（多批累积、bash 独占、subagent
  放行等既有语义）；
- ``serial``：按顺序逐个执行（确定性顺序，工具间无并发）；
- ``parallel``：全部并发执行（Semaphore 限流 + FIRST_EXCEPTION 级联取消）。

**清单接管**：``tool_scheduler`` 聚合插件收到组合根注入的
``managed_tool_engines``（清单已接管的 id，含被禁用的）时经
``set_managed_builtin_tool_engines`` 声明这些 id 由清单条目负责；无清单
（单元测试、独立调用）时无接管，全部内置引擎默认生效。
"""

from __future__ import annotations

import importlib
import logging
import threading
from typing import Any, Callable, Dict, List, Tuple

_logger = logging.getLogger(__name__)

_lock = threading.RLock()
_ABSENT = object()

#: 默认引擎（未显式指定时使用）
DEFAULT_ENGINE = "dag"

#: (id, 模块, 属性名)
_BUILTIN_SPECS: Tuple[Tuple[str, str, str], ...] = (
    ("dag", "src.core.tool_engines", "_dag_engine"),
    ("serial", "src.core.tool_engines", "_serial_engine"),
    ("parallel", "src.core.tool_engines", "_parallel_engine"),
)


def _make_factory(module_name: str, attr: str):
    def _factory():
        module = importlib.import_module(module_name)
        return getattr(module, attr)

    _factory.__name__ = f"_builtin_{attr}_factory"
    return _factory


_builtin_specs: Dict[str, Callable[[], Any]] = {
    spec_id: _make_factory(module_name, attr)
    for spec_id, module_name, attr in _BUILTIN_SPECS
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
            raise KeyError(f"未知内置工具执行引擎: {item!r}（可用: {list(_builtin_specs)}）")
        selected.append(item)
    return selected


def builtin_tool_engine_ids() -> list[str]:
    return list(_builtin_specs)


def default_tool_engine_factory(spec_id: str) -> Callable[[], Any]:
    try:
        return _builtin_specs[spec_id]
    except KeyError:
        raise KeyError(f"未知内置工具执行引擎: {spec_id!r}（可用: {list(_builtin_specs)}）") from None


def builtin_tool_engine_factories() -> Dict[str, Callable[[], Any]]:
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


def register_builtin_tool_engine(spec_id: str, factory: Any = None) -> Callable[[], None]:
    if spec_id not in _builtin_specs:
        raise KeyError(f"未知内置工具执行引擎: {spec_id!r}（可用: {list(_builtin_specs)}）")
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


def unregister_builtin_tool_engine(spec_id: str) -> bool:
    with _lock:
        return _registered_builtin.pop(spec_id, None) is not None


def set_managed_builtin_tool_engines(ids) -> Callable[[], None]:
    selected = _normalize_ids(ids)
    with _lock:
        added = [item for item in selected if item not in _managed_builtin]
        _managed_builtin.update(added)

    def _undo() -> None:
        with _lock:
            for item in added:
                _managed_builtin.discard(item)

    return _undo


def managed_tool_engine_ids() -> list[str]:
    with _lock:
        return sorted(_managed_builtin)


def disable_builtin_tool_engines(ids) -> Callable[[], None]:
    selected = _normalize_ids(ids)
    with _lock:
        added = [item for item in selected if item not in _disabled_builtin]
        _disabled_builtin.update(added)

    def _undo() -> None:
        with _lock:
            for item in added:
                _disabled_builtin.discard(item)

    return _undo


def register_tool_engine(name: str, factory: Callable[[], Any]) -> Callable[[], None]:
    """注册一个扩展引擎（``factory() -> engine``）；返回幂等撤销。"""
    if not isinstance(name, str) or not name:
        raise ValueError(f"引擎名必须是非空字符串: {name!r}")
    if not callable(factory):
        raise TypeError(f"引擎工厂必须可调用: {factory!r}")
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


def tool_engine_factories() -> Dict[str, Callable[[], Any]]:
    with _lock:
        return dict(_extension)


def engine_names() -> List[str]:
    with _lock:
        return sorted(set(builtin_tool_engine_factories()) | set(_extension))


def resolve_tool_engine(name: str):
    """解析引擎可调用（扩展 → 生效内置 → None）。"""
    with _lock:
        factory = _extension.get(name)
        if factory is None:
            factory = builtin_tool_engine_factories().get(name)
    if factory is None:
        return None
    return factory()


def default_tool_engine():
    """默认引擎（``dag``）；即便被接管/禁用也回退内置实现，保证调度不中断。"""
    engine = resolve_tool_engine(DEFAULT_ENGINE)
    if engine is not None:
        return engine
    return _dag_engine


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


# ── 内置引擎实现 ───────────────────────────────────────────


async def _dag_engine(scheduler, tool_calls, *, agent_ref, on_before=None,
                      on_after=None, run_method=None, is_outermost=True):
    """全局 DAG 拓扑分层调度（默认引擎）。"""
    return await scheduler._schedule_via_dag(
        tool_calls,
        agent_ref=agent_ref,
        on_before=on_before,
        on_after=on_after,
        run_method=run_method,
        is_outermost=is_outermost,
    )


async def _serial_engine(scheduler, tool_calls, *, agent_ref, on_before=None,
                         on_after=None, run_method=None, is_outermost=True):
    """串行引擎：按顺序逐个执行，保证确定性顺序、工具间无并发。"""
    results = []
    for tc in tool_calls:
        results.append(
            await scheduler._execute_one_async(
                tc, agent_ref=agent_ref,
                on_before=on_before, on_after=on_after, run_method=run_method,
            )
        )
    return results


async def _parallel_engine(scheduler, tool_calls, *, agent_ref, on_before=None,
                           on_after=None, run_method=None, is_outermost=True):
    """并行引擎：全部并发执行（Semaphore 限流 + FIRST_EXCEPTION 级联取消）。"""
    return await scheduler._execute_concurrent(
        tool_calls, agent_ref=agent_ref,
        on_before=on_before, on_after=on_after, run_method=run_method,
    )


__all__ = [
    "DEFAULT_ENGINE",
    "builtin_tool_engine_ids",
    "builtin_tool_engine_factories",
    "default_tool_engine_factory",
    "register_builtin_tool_engine",
    "unregister_builtin_tool_engine",
    "set_managed_builtin_tool_engines",
    "managed_tool_engine_ids",
    "disable_builtin_tool_engines",
    "register_tool_engine",
    "tool_engine_factories",
    "engine_names",
    "resolve_tool_engine",
    "default_tool_engine",
    "clear",
    "reset",
]
