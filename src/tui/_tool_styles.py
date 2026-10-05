"""工具 / 类别 / Agent 类型表现注册表 — 单一来源（一切皆插件）。

「一切皆插件」：工具表现映射（工具名 → 类别/图标）、类别配色、Agent 类型
缩写/配色不再是 ``src.tui._tool_icons`` 的硬编码字典，而是注册到本模块的
规格表；每一个内置项由清单中的**独立插件条目**（``tool_style``）显式注册，
因而可被 Profile/Bundle 声明，也可被 Patch/Overlay 按 id 单独禁用、覆盖
（改类别/图标/配色）或替换。

**清单接管**：``tool_styles`` 聚合插件收到组合根注入的 ``managed_tool_styles``
（清单已接管的 id，含被禁用的）时经 ``set_managed_builtin_presentations`` 声明
这些 id 由清单条目负责——对应内置项不再走默认装配；被禁用（未挂载）的条目
因此真正缺席。无清单（单元测试、独立调用）时无接管，全部内置项默认生效。

三种条目 kind：

- ``tool``：``name`` = 工具名，``category`` / ``icon``；
- ``category``：``name`` = 类别名，``fg`` = 256 色号；
- ``agent``：``name`` = Agent 类型名，``abbrev`` / ``fg``。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from .core.style import Style

_lock = threading.RLock()
_ABSENT = object()


@dataclass(frozen=True)
class PresentationSpec:
    """工具 / 类别 / Agent 类型表现规格。"""

    id: str
    kind: str
    name: str
    category: str = ""
    icon: str = ""
    abbrev: str = ""
    fg: int = 0

    def to_dict(self) -> dict:
        data = {"id": self.id, "kind": self.kind, "name": self.name}
        if self.kind == "tool":
            data["category"] = self.category
            data["icon"] = self.icon
        elif self.kind == "category":
            data["fg"] = self.fg
        elif self.kind == "agent":
            data["abbrev"] = self.abbrev
            data["fg"] = self.fg
        return data


def _tool(tool: str, category: str, icon: str) -> PresentationSpec:
    return PresentationSpec(id=f"tool_{tool}", kind="tool", name=tool, category=category, icon=icon)


def _category(category: str, fg: int) -> PresentationSpec:
    return PresentationSpec(id=f"cat_{category}", kind="category", name=category, fg=fg)


def _agent(agent_type: str, abbrev: str, fg: int) -> PresentationSpec:
    return PresentationSpec(id=f"agent_{agent_type}", kind="agent", name=agent_type, abbrev=abbrev, fg=fg)


#: 内置表现声明（工具 / 类别 / Agent 类型）
_BUILTIN_SPECS: Tuple[PresentationSpec, ...] = (
    # ── 工具 → 类别 / 图标 ──
    _tool("bash", "shell", "\u26a1"),
    _tool("execute_command", "shell", "\u26a1"),
    _tool("read_file", "file_read", "\U0001f4d6"),
    _tool("write_file", "file_write", "\u270e"),
    _tool("update_file", "file_write", "\u270e"),
    _tool("str_replace_editor", "file_write", "\u270e"),
    _tool("file_editor", "file_write", "\u270e"),
    _tool("subagent", "agent", "\u2699"),
    _tool("subagent_opt", "agent", "\u2699"),
    _tool("user_select", "interact", "\u2753"),
    _tool("web_search", "search", "\U0001f310"),
    _tool("web_fetch", "search", "\U0001f310"),
    _tool("rm", "delete", "\u2715"),
    _tool("grep", "search", "\u2315"),
    _tool("find", "search", "\u2315"),
    _tool("glob", "search", "\u2315"),
    # ── 类别 → 配色 ──
    _category("shell", 41),
    _category("file_read", 81),
    _category("file_write", 213),
    _category("search", 221),
    _category("agent", 75),
    _category("interact", 51),
    _category("delete", 203),
    # ── Agent 类型 → 缩写 / 配色 ──
    _agent("map", "mp", 33),
    _agent("review", "rv", 129),
    _agent("plan", "pl", 214),
    _agent("execute", "ex", 208),
)

_builtin_specs: Dict[str, PresentationSpec] = {spec.id: spec for spec in _BUILTIN_SPECS}

_registered_builtin: Dict[str, PresentationSpec] = {}
_managed_builtin: set = set()
_disabled_builtin: set = set()
_extension: Dict[str, PresentationSpec] = {}

# 派生查询缓存（任何变更后失效）
_cache: Optional[dict] = None


def _normalize_ids(ids) -> List[str]:
    if isinstance(ids, str):
        ids = [ids]
    selected: List[str] = []
    for item in ids or ():
        if item not in _builtin_specs:
            raise KeyError(f"未知内置表现条目: {item!r}（可用: {list(_builtin_specs)}）")
        selected.append(item)
    return selected


def builtin_presentation_ids() -> list[str]:
    """全部内置表现条目 id（按声明顺序）。"""
    return list(_builtin_specs)


def default_presentation(spec_id: str) -> PresentationSpec:
    try:
        return _builtin_specs[spec_id]
    except KeyError:
        raise KeyError(f"未知内置表现条目: {spec_id!r}（可用: {list(_builtin_specs)}）") from None


def active_presentations() -> Dict[str, PresentationSpec]:
    """当前生效的内置表现条目（``id → 规格``；按声明顺序）。"""
    with _lock:
        result: Dict[str, PresentationSpec] = {}
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


def _invalidate() -> None:
    global _cache
    _cache = None


def register_builtin_presentation(spec_id: str, spec: Optional[PresentationSpec] = None) -> Callable[[], None]:
    """注册/覆盖一个内置表现条目（``spec=None`` 用默认规格）；返回幂等撤销。"""
    if spec_id not in _builtin_specs:
        raise KeyError(f"未知内置表现条目: {spec_id!r}（可用: {list(_builtin_specs)}）")
    with _lock:
        previous = _registered_builtin.get(spec_id, _ABSENT)
        _registered_builtin[spec_id] = spec if spec is not None else _builtin_specs[spec_id]
        _invalidate()

    def _undo() -> None:
        with _lock:
            if previous is _ABSENT:
                _registered_builtin.pop(spec_id, None)
            else:
                _registered_builtin[spec_id] = previous
            _invalidate()

    return _undo


def unregister_builtin_presentation(spec_id: str) -> bool:
    with _lock:
        removed = _registered_builtin.pop(spec_id, None) is not None
        if removed:
            _invalidate()
    return removed


def set_managed_builtin_presentations(ids) -> Callable[[], None]:
    """声明这些内置 id 由清单条目负责（默认装配被抑制）；返回撤销。"""
    selected = _normalize_ids(ids)
    with _lock:
        added = [item for item in selected if item not in _managed_builtin]
        _managed_builtin.update(added)
        _invalidate()

    def _undo() -> None:
        with _lock:
            for item in added:
                _managed_builtin.discard(item)
            _invalidate()

    return _undo


def managed_presentation_ids() -> list[str]:
    with _lock:
        return sorted(_managed_builtin)


def disable_builtin_presentations(ids) -> Callable[[], None]:
    """禁用一个或多个内置表现条目（返回幂等撤销）。"""
    selected = _normalize_ids(ids)
    with _lock:
        added = [item for item in selected if item not in _disabled_builtin]
        _disabled_builtin.update(added)
        _invalidate()

    def _undo() -> None:
        with _lock:
            for item in added:
                _disabled_builtin.discard(item)
            _invalidate()

    return _undo


def register_presentation(spec: PresentationSpec) -> Callable[[], None]:
    """注册一个扩展表现条目（id 覆盖内置 / 新增）；返回幂等撤销。"""
    if not isinstance(spec, PresentationSpec):
        raise TypeError(f"扩展表现条目必须是 PresentationSpec: {spec!r}")
    with _lock:
        previous = _extension.get(spec.id, _ABSENT)
        _extension[spec.id] = spec
        _invalidate()

    def _undo() -> None:
        with _lock:
            if previous is _ABSENT:
                _extension.pop(spec.id, None)
            else:
                _extension[spec.id] = previous
            _invalidate()

    return _undo


def unregister_presentation(spec_id: str) -> bool:
    with _lock:
        removed = _extension.pop(spec_id, None) is not None
        if removed:
            _invalidate()
    return removed


def _active_maps() -> dict:
    """构造并缓存查询映射（扩展覆盖内置；扩展可新增键）。"""
    global _cache
    with _lock:
        if _cache is not None:
            return _cache
        tool_category_map: Dict[str, str] = {}
        tool_icon_map: Dict[str, str] = {}
        category_style_map: Dict[str, Style] = {}
        agent_abbrev: Dict[str, str] = {}
        agent_style_map: Dict[str, Style] = {}

        def _put(spec: PresentationSpec) -> None:
            if spec.kind == "tool":
                tool_category_map[spec.name] = spec.category
                tool_icon_map[spec.name] = spec.icon
            elif spec.kind == "category":
                category_style_map[spec.name] = Style(fg=spec.fg)
            elif spec.kind == "agent":
                agent_abbrev[spec.name] = spec.abbrev
                agent_style_map[spec.name] = Style(fg=spec.fg)

        for spec in active_presentations().values():
            _put(spec)
        for spec in _extension.values():
            _put(spec)
        _cache = {
            "tool_category": tool_category_map,
            "tool_icon": tool_icon_map,
            "category_style": category_style_map,
            "agent_abbrev": agent_abbrev,
            "agent_style": agent_style_map,
        }
        return _cache


def tool_category(tool_name: str) -> str:
    """工具名 → 类别（未知名返回空串）。"""
    return _active_maps()["tool_category"].get(tool_name, "")


def tool_icon(tool_name: str) -> str:
    """工具名 → 图标（未知名返回空串）。"""
    return _active_maps()["tool_icon"].get(tool_name, "")


def category_style(category: str) -> Optional[Style]:
    """类别 → 静态 Style（未知类别返回 None）。"""
    return _active_maps()["category_style"].get(category)


def tool_style(tool_name: str) -> Optional[Style]:
    """工具名 → 类别 Style（未知名/未分类返回 None）。"""
    category = tool_category(tool_name)
    if not category:
        return None
    return category_style(category)


def agent_type_abbrev(agent_type: str) -> str:
    """Agent 类型 → 缩写（未知类型返回空串）。"""
    return _active_maps()["agent_abbrev"].get(agent_type, "")


def agent_type_style(agent_type: str) -> Optional[Style]:
    """Agent 类型 → 静态 Style（未知类型返回 None）。"""
    return _active_maps()["agent_style"].get(agent_type)


def extension_presentations() -> Dict[str, PresentationSpec]:
    with _lock:
        return dict(_extension)


def clear() -> None:
    """清空扩展项与清单注册（测试用；不影响内置默认与禁用状态）。"""
    with _lock:
        _extension.clear()
        _registered_builtin.clear()
        _invalidate()


def reset() -> None:
    """重置全部状态到「无清单、无禁用、全部默认」（测试隔离用）。"""
    with _lock:
        _extension.clear()
        _registered_builtin.clear()
        _managed_builtin.clear()
        _disabled_builtin.clear()
        _invalidate()


__all__ = [
    "PresentationSpec",
    "builtin_presentation_ids",
    "default_presentation",
    "active_presentations",
    "register_builtin_presentation",
    "unregister_builtin_presentation",
    "set_managed_builtin_presentations",
    "managed_presentation_ids",
    "disable_builtin_presentations",
    "register_presentation",
    "unregister_presentation",
    "extension_presentations",
    "tool_category",
    "tool_icon",
    "tool_style",
    "category_style",
    "agent_type_abbrev",
    "agent_type_style",
    "clear",
    "reset",
]
