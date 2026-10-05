"""Preset 注册表 — 每会话能力组合的单一来源（一切皆插件）。

对应 dsh 的 per-session preset：同一进程里不同会话可以跑不同的工具/persona
组合。每个内置 preset（``standard`` / ``minimal`` / ``code``）由本模块**声明**，
并由清单中的**独立插件条目**（``preset``，经 ``src.plugins.preset_entries``）
显式注册——因此可被 Profile/Bundle 声明，也可被 Patch/Overlay 按 id 单独禁用、
覆盖或替换，而非硬编码在 ``PluginsService`` 里。

**清单接管**：``presets`` 聚合插件收到组合根注入的 ``managed_presets``（清单已
接管的 id，含被禁用的）时经 ``set_managed_builtin_presets`` 声明这些 id 由清单
条目负责——对应内置 preset 不再走默认装配；被禁用（未挂载）的条目因此真正缺席。
无清单（单元测试、独立调用）时无接管，全部内置 preset 默认生效。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

_lock = threading.RLock()
_ABSENT = object()


@dataclass(frozen=True)
class Preset:
    """一个能力组合预设。"""

    name: str
    description: str = ""
    tool_excludes: Tuple[str, ...] = ()
    tool_includes: Tuple[str, ...] = ()
    model: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "tool_excludes": list(self.tool_excludes),
            "tool_includes": list(self.tool_includes),
            "model": self.model,
        }

    def allows(self, tool_name: str) -> bool:
        if self.tool_includes and tool_name not in self.tool_includes:
            return False
        return tool_name not in self.tool_excludes


#: 内置 preset 声明（id → 默认规格）——每项由清单中的独立插件条目注册。
_BUILTIN_PRESET_SPECS: Dict[str, Preset] = {
    "standard": Preset("standard", "标准模式：全部工具可用（默认）"),
    "minimal": Preset(
        "minimal",
        "最小模式：只读 + 基础交互工具",
        tool_excludes=(
            "bash", "bash_opt", "subagent", "subagent_opt", "user_select",
            "web_search", "write_file", "update_file", "rm", "mv", "cp", "mkdir",
        ),
    ),
    "code": Preset(
        "code",
        "编码模式：读写 + shell，无网络与委派",
        tool_excludes=("web_search", "subagent", "subagent_opt", "user_select"),
    ),
}

_registered_builtin: Dict[str, Preset] = {}
_managed_builtin: set = set()
_disabled_builtin: set = set()
_extension: Dict[str, Preset] = {}


def builtin_preset_names() -> List[str]:
    """全部内置 preset 名（含被接管/禁用的，按声明顺序）。"""
    return list(_BUILTIN_PRESET_SPECS)


def _normalize_names(names) -> List[str]:
    if isinstance(names, str):
        names = [names]
    selected: List[str] = []
    for item in names or ():
        if item not in _BUILTIN_PRESET_SPECS:
            raise KeyError(f"未知内置 preset: {item!r}（可用: {list(_BUILTIN_PRESET_SPECS)}）")
        selected.append(item)
    return selected


def default_preset(name: str) -> Preset:
    try:
        return _BUILTIN_PRESET_SPECS[name]
    except KeyError:
        raise KeyError(f"未知内置 preset: {name!r}（可用: {list(_BUILTIN_PRESET_SPECS)}）") from None


def active_presets() -> Dict[str, Preset]:
    """当前生效的 preset（内置装配 + 扩展），按名字。"""
    with _lock:
        result: Dict[str, Preset] = {}
        for name, default in _BUILTIN_PRESET_SPECS.items():
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


def resolve_preset(name: str) -> Optional[Preset]:
    return active_presets().get(name)


def register_builtin_preset(name: str, preset: Optional[Preset] = None) -> Callable[[], None]:
    """注册/覆盖一个内置 preset（``preset=None`` 用默认声明）；返回幂等撤销。"""
    if name not in _BUILTIN_PRESET_SPECS:
        raise KeyError(f"未知内置 preset: {name!r}（可用: {list(_BUILTIN_PRESET_SPECS)}）")
    effective = preset if preset is not None else _BUILTIN_PRESET_SPECS[name]
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


def unregister_builtin_preset(name: str) -> bool:
    with _lock:
        return _registered_builtin.pop(name, None) is not None


def set_managed_builtin_presets(names) -> Callable[[], None]:
    """声明这些内置 preset id 由清单条目负责（默认装配被抑制）；返回撤销。"""
    selected = _normalize_names(names)
    with _lock:
        added = [item for item in selected if item not in _managed_builtin]
        _managed_builtin.update(added)

    def _undo() -> None:
        with _lock:
            for item in added:
                _managed_builtin.discard(item)

    return _undo


def managed_preset_names() -> List[str]:
    with _lock:
        return sorted(_managed_builtin)


def disable_builtin_presets(names) -> Callable[[], None]:
    """禁用一个或多个内置 preset（返回幂等撤销）。"""
    selected = _normalize_names(names)
    with _lock:
        added = [item for item in selected if item not in _disabled_builtin]
        _disabled_builtin.update(added)

    def _undo() -> None:
        with _lock:
            for item in added:
                _disabled_builtin.discard(item)

    return _undo


def register_preset(preset: Preset) -> Callable[[], None]:
    """注册一个扩展 preset（返回幂等撤销）。"""
    if not isinstance(preset, Preset):
        raise TypeError(f"preset 规格非法: {preset!r}")
    if not preset.name:
        raise ValueError("preset 名必须是非空字符串")
    with _lock:
        previous = _extension.get(preset.name, _ABSENT)
        _extension[preset.name] = preset

    def _undo() -> None:
        with _lock:
            if previous is _ABSENT:
                _extension.pop(preset.name, None)
            else:
                _extension[preset.name] = previous

    return _undo


def unregister_preset(name: str) -> bool:
    with _lock:
        return _extension.pop(name, None) is not None


def preset_names() -> List[str]:
    return sorted(active_presets())


def clear() -> None:
    """清空扩展 preset 与清单注册（测试用；不影响内置默认与禁用状态）。"""
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
    "Preset",
    "builtin_preset_names",
    "default_preset",
    "active_presets",
    "resolve_preset",
    "preset_names",
    "register_builtin_preset",
    "unregister_builtin_preset",
    "set_managed_builtin_presets",
    "managed_preset_names",
    "disable_builtin_presets",
    "register_preset",
    "unregister_preset",
    "clear",
    "reset",
]
