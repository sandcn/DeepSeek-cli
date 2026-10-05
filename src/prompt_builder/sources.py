"""提示词来源注册表 — 各 Agent 静态提词文件的单一来源（一切皆插件）。

子代理的静态提词文件映射（``map`` → ``prompts_export_map`` 等）不再硬编码在
``builder.py`` 的每个 ``build_*_agent_system_prompt`` 里，而是由本模块**声明**，
并由清单中的**独立插件条目**（``prompt_source``，经 ``src.plugins.prompt_entries``）
显式注册——因此可被 Profile/Bundle 声明，也可被 Patch/Overlay 按 id 单独禁用、
覆盖或替换。

主 Agent 的提词来源由**运行模式**决定（见 ``modes.py``），不在本表内；本表只
覆盖子代理（``map`` / ``review`` / ``plan`` / ``execute``）与通用子代理（``sub``，
无独立文件，用兜底提示词）。

**清单接管**：``prompt`` 聚合插件收到组合根注入的 ``managed_prompt_sources``
（清单已接管的 id，含被禁用的）时经 ``set_managed_builtin_prompt_sources`` 声明
这些 id 由清单条目负责；无清单（单元测试、独立调用）时无接管，全部内置来源
默认生效。
"""

from __future__ import annotations

import threading
from typing import Callable, Dict, List, Optional

_lock = threading.RLock()
_ABSENT = object()

#: 内置提示词来源声明（agent 名 → prompts 文件基名；空串=无文件用兜底）
_BUILTIN_PROMPT_SOURCES: Dict[str, str] = {
    "sub": "",
    "map": "prompts_export_map",
    "review": "prompts_export_review",
    "plan": "prompts_export_plan",
    "execute": "prompts_export_execute",
}

_registered_builtin: Dict[str, str] = {}
_managed_builtin: set = set()
_disabled_builtin: set = set()
_extension: Dict[str, str] = {}


def builtin_prompt_source_ids() -> List[str]:
    """全部内置提示词来源 id（含被接管/禁用的，按声明顺序）。"""
    return list(_BUILTIN_PROMPT_SOURCES)


def _normalize_names(names) -> List[str]:
    if isinstance(names, str):
        names = [names]
    selected: List[str] = []
    for item in names or ():
        if item not in _BUILTIN_PROMPT_SOURCES:
            raise KeyError(
                f"未知内置提示词来源: {item!r}（可用: {list(_BUILTIN_PROMPT_SOURCES)}）"
            )
        selected.append(item)
    return selected


def default_prompt_source(agent: str) -> str:
    try:
        return _BUILTIN_PROMPT_SOURCES[agent]
    except KeyError:
        raise KeyError(
            f"未知内置提示词来源: {agent!r}（可用: {list(_BUILTIN_PROMPT_SOURCES)}）"
        ) from None


def active_prompt_sources() -> Dict[str, str]:
    """当前生效的提示词来源（内置装配 + 扩展），按 agent 名。"""
    with _lock:
        result: Dict[str, str] = {}
        for name, default in _BUILTIN_PROMPT_SOURCES.items():
            if name in _disabled_builtin:
                continue
            if name in _registered_builtin:
                result[name] = _registered_builtin[name]
                continue
            if name in _managed_builtin:
                continue
            result[name] = default
        result.update(_extension)
        return result


def resolve_prompt_source(agent: str) -> Optional[str]:
    """解析某 agent 的静态提词文件基名；未知返回 None。"""
    return active_prompt_sources().get(agent)


def register_builtin_prompt_source(agent: str, export: Optional[str] = None) -> Callable[[], None]:
    """注册/覆盖一个内置提示词来源；返回幂等撤销。

    Args:
        agent: 内置 agent 名。
        export: prompts 文件基名（``None`` 用默认声明；空串表示用兜底提示词）。
    """
    if agent not in _BUILTIN_PROMPT_SOURCES:
        raise KeyError(
            f"未知内置提示词来源: {agent!r}（可用: {list(_BUILTIN_PROMPT_SOURCES)}）"
        )
    effective = _BUILTIN_PROMPT_SOURCES[agent] if export is None else str(export)
    with _lock:
        previous = _registered_builtin.get(agent, _ABSENT)
        _registered_builtin[agent] = effective

    def _undo() -> None:
        with _lock:
            if previous is _ABSENT:
                _registered_builtin.pop(agent, None)
            else:
                _registered_builtin[agent] = previous

    return _undo


def unregister_builtin_prompt_source(agent: str) -> bool:
    with _lock:
        return _registered_builtin.pop(agent, None) is not None


def set_managed_builtin_prompt_sources(names) -> Callable[[], None]:
    """声明这些内置来源 id 由清单条目负责（默认装配被抑制）；返回撤销。"""
    selected = _normalize_names(names)
    with _lock:
        added = [item for item in selected if item not in _managed_builtin]
        _managed_builtin.update(added)

    def _undo() -> None:
        with _lock:
            for item in added:
                _managed_builtin.discard(item)

    return _undo


def managed_prompt_source_ids() -> List[str]:
    with _lock:
        return sorted(_managed_builtin)


def disable_builtin_prompt_sources(names) -> Callable[[], None]:
    """禁用一个或多个内置提示词来源（返回幂等撤销）。"""
    selected = _normalize_names(names)
    with _lock:
        added = [item for item in selected if item not in _disabled_builtin]
        _disabled_builtin.update(added)

    def _undo() -> None:
        with _lock:
            for item in added:
                _disabled_builtin.discard(item)

    return _undo


def register_prompt_source(agent: str, export: str) -> Callable[[], None]:
    """注册一个扩展提示词来源（返回幂等撤销）。"""
    agent = str(agent or "").strip()
    if not agent:
        raise ValueError("提示词来源 agent 名必须是非空字符串")
    with _lock:
        previous = _extension.get(agent, _ABSENT)
        _extension[agent] = str(export or "")

    def _undo() -> None:
        with _lock:
            if previous is _ABSENT:
                _extension.pop(agent, None)
            else:
                _extension[agent] = previous

    return _undo


def unregister_prompt_source(agent: str) -> bool:
    with _lock:
        return _extension.pop(agent, None) is not None


def clear() -> None:
    """清空扩展来源与清单注册（测试用；不影响内置默认与禁用状态）。"""
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
    "builtin_prompt_source_ids",
    "default_prompt_source",
    "active_prompt_sources",
    "resolve_prompt_source",
    "register_builtin_prompt_source",
    "unregister_builtin_prompt_source",
    "set_managed_builtin_prompt_sources",
    "managed_prompt_source_ids",
    "disable_builtin_prompt_sources",
    "register_prompt_source",
    "unregister_prompt_source",
    "clear",
    "reset",
]
