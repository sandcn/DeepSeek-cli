"""主 Agent 运行模式注册表 — 模式元数据的单一来源（一切皆插件）。

主 Agent 的运行模式（``empty`` / ``simple`` / ``standard``，Ctrl+B 循环切换）
不再硬编码在 ``builder.py`` 的字典里，而是由本模块**声明**，并由清单中的
**独立插件条目**（``prompt_mode``，经 ``src.plugins.prompt_entries``）显式
注册——因此可被 Profile/Bundle 声明，也可被 Patch/Overlay 按 id 单独禁用、
覆盖（label/export/order）或替换。

当前模式**状态**仍由 ``builder._MODE`` 持有（模块级真源，供 TUI 每帧读取）；
本模块只提供模式的元数据与解析，避免引入第二处可变状态。

**清单接管**：``prompt`` 聚合插件收到组合根注入的 ``managed_prompt_modes``
（清单已接管的 id，含被禁用的）时经 ``set_managed_builtin_modes`` 声明这些 id
由清单条目负责；无清单（单元测试、独立调用）时无接管，全部内置模式默认生效。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

_lock = threading.RLock()
_ABSENT = object()

#: 默认模式（未显式指定 / 非法值时的回退）
DEFAULT_MODE = "empty"


@dataclass(frozen=True)
class AgentMode:
    """一个主 Agent 运行模式。"""

    name: str
    label: str
    export: str
    order: int = 0

    def to_dict(self) -> dict:
        return {"name": self.name, "label": self.label, "export": self.export, "order": self.order}


#: 内置模式声明（id → 默认规格）——每项由清单中的独立插件条目注册。
_BUILTIN_MODE_SPECS: Dict[str, AgentMode] = {
    "empty": AgentMode("empty", "空模式", "prompts_export_main_empty", 0),
    "simple": AgentMode("simple", "简单模式", "prompts_export_main_simple", 1),
    "standard": AgentMode("standard", "标准模式", "prompts_export_main", 2),
}

_registered_builtin: Dict[str, AgentMode] = {}
_managed_builtin: set = set()
_disabled_builtin: set = set()
_extension: Dict[str, AgentMode] = {}


def builtin_mode_names() -> List[str]:
    """全部内置模式名（含被接管/禁用的，按声明顺序）。"""
    return list(_BUILTIN_MODE_SPECS)


def _normalize_names(names) -> List[str]:
    if isinstance(names, str):
        names = [names]
    selected: List[str] = []
    for item in names or ():
        if item not in _BUILTIN_MODE_SPECS:
            raise KeyError(f"未知内置运行模式: {item!r}（可用: {list(_BUILTIN_MODE_SPECS)}）")
        selected.append(item)
    return selected


def default_mode(name: str) -> AgentMode:
    try:
        return _BUILTIN_MODE_SPECS[name]
    except KeyError:
        raise KeyError(f"未知内置运行模式: {name!r}（可用: {list(_BUILTIN_MODE_SPECS)}）") from None


def active_modes() -> Dict[str, AgentMode]:
    """当前生效的模式（内置装配 + 扩展），按名字。"""
    with _lock:
        result: Dict[str, AgentMode] = {}
        for name, default in _BUILTIN_MODE_SPECS.items():
            if name in _disabled_builtin:
                continue
            override = _registered_builtin.get(name)
            if override is not None:
                result[name] = override
                continue
            if name in _managed_builtin:
                continue
            result[name] = default
        result.update(_extension)
        return result


def mode_order() -> List[str]:
    """当前生效模式按 ``order`` 排序的名字列表（Ctrl+B 循环顺序）。"""
    modes = active_modes()
    return sorted(modes, key=lambda name: (modes[name].order, name))


def resolve_mode(name: Optional[str]) -> AgentMode:
    """解析模式规格；未知/空回退默认模式（``empty``）。"""
    modes = active_modes()
    return modes.get(name or "") or modes.get(DEFAULT_MODE) or _BUILTIN_MODE_SPECS[DEFAULT_MODE]


def resolve_mode_export(name: Optional[str]) -> str:
    return resolve_mode(name).export


def resolve_mode_label(name: Optional[str]) -> str:
    return resolve_mode(name).label


def register_builtin_mode(name: str, mode: Optional[AgentMode] = None) -> Callable[[], None]:
    """注册/覆盖一个内置模式（``mode=None`` 用默认声明）；返回幂等撤销。"""
    if name not in _BUILTIN_MODE_SPECS:
        raise KeyError(f"未知内置运行模式: {name!r}（可用: {list(_BUILTIN_MODE_SPECS)}）")
    effective = mode if mode is not None else _BUILTIN_MODE_SPECS[name]
    with _lock:
        previous = _registered_builtin.get(name, _ABSENT)
        _registered_builtin[name] = effective

    def _undo() -> None:
        with _lock:
            if previous is _ABSENT:
                _registered_builtin.pop(name, None)
            else:
                _registered_builtin[name] = previous

    return _undo


def unregister_builtin_mode(name: str) -> bool:
    with _lock:
        return _registered_builtin.pop(name, None) is not None


def set_managed_builtin_modes(names) -> Callable[[], None]:
    """声明这些内置模式 id 由清单条目负责（默认装配被抑制）；返回撤销。"""
    selected = _normalize_names(names)
    with _lock:
        added = [item for item in selected if item not in _managed_builtin]
        _managed_builtin.update(added)

    def _undo() -> None:
        with _lock:
            for item in added:
                _managed_builtin.discard(item)

    return _undo


def managed_mode_names() -> List[str]:
    with _lock:
        return sorted(_managed_builtin)


def disable_builtin_modes(names) -> Callable[[], None]:
    """禁用一个或多个内置模式（返回幂等撤销）。"""
    selected = _normalize_names(names)
    with _lock:
        added = [item for item in selected if item not in _disabled_builtin]
        _disabled_builtin.update(added)

    def _undo() -> None:
        with _lock:
            for item in added:
                _disabled_builtin.discard(item)

    return _undo


def register_mode(mode: AgentMode) -> Callable[[], None]:
    """注册一个扩展模式（返回幂等撤销）。"""
    if not isinstance(mode, AgentMode):
        raise TypeError(f"模式规格非法: {mode!r}")
    if not mode.name:
        raise ValueError("模式名必须是非空字符串")
    with _lock:
        previous = _extension.get(mode.name, _ABSENT)
        _extension[mode.name] = mode

    def _undo() -> None:
        with _lock:
            if previous is _ABSENT:
                _extension.pop(mode.name, None)
            else:
                _extension[mode.name] = previous

    return _undo


def unregister_mode(name: str) -> bool:
    with _lock:
        return _extension.pop(name, None) is not None


def clear() -> None:
    """清空扩展模式与清单注册（测试用；不影响内置默认与禁用状态）。"""
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
    "AgentMode",
    "DEFAULT_MODE",
    "builtin_mode_names",
    "default_mode",
    "active_modes",
    "mode_order",
    "resolve_mode",
    "resolve_mode_export",
    "resolve_mode_label",
    "register_builtin_mode",
    "unregister_builtin_mode",
    "set_managed_builtin_modes",
    "managed_mode_names",
    "disable_builtin_modes",
    "register_mode",
    "unregister_mode",
    "clear",
    "reset",
]
