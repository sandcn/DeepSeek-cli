"""状态栏段注册表 — StatusBar 段构建器的单一来源（一切皆插件）。

「一切皆插件」：状态栏的各个信息段（模型名 / 工具计数 / 耗时 / token / 速度）
不再硬编码在 ``status_bar._build_status_runs`` 里，而是注册到本模块的规格表；
每个段由清单中的**独立插件条目**（``status_segment``）显式注册，因而可被
Profile/Bundle 声明，也可被 Patch/Overlay 按 id 单独禁用、替换（整段实现替换）。

**清单接管**：``status_segments`` 聚合插件收到组合根注入的
``managed_status_segments``（清单已接管的 id，含被禁用的）时经
``set_managed_builtin_segments`` 声明这些 id 由清单条目负责——对应内置段不再走
默认装配；被禁用（未挂载）的条目因此真正缺席。无清单（单元测试、独立调用）时
无接管，全部内置段默认生效。

段实现签名为 ``handler(ctx) -> list[StyledRun] | None``，``ctx`` 为
``StatusContext``。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Dict, List, Tuple

_lock = threading.RLock()
_ABSENT = object()


@dataclass(frozen=True)
class StatusSegment:
    """一个状态栏段规格。"""

    id: str
    order: int
    handler: str

    def to_dict(self) -> dict:
        return {"id": self.id, "order": self.order, "handler": self.handler}


#: 内置段声明（order 决定显示顺序）
_BUILTIN_SPECS: Tuple[StatusSegment, ...] = (
    StatusSegment("model", 0, "src.tui.app.status_bar:_model_segment"),
    StatusSegment("tools", 10, "src.tui.app.status_bar:_tools_segment"),
    StatusSegment("elapsed", 20, "src.tui.app.status_bar:_elapsed_segment"),
    StatusSegment("tokens", 30, "src.tui.app.status_bar:_tokens_segment"),
    StatusSegment("speed", 40, "src.tui.app.status_bar:_speed_segment"),
)

_builtin_specs: Dict[str, StatusSegment] = {spec.id: spec for spec in _BUILTIN_SPECS}

_registered_builtin: Dict[str, StatusSegment] = {}
_managed_builtin: set = set()
_disabled_builtin: set = set()
_extension: Dict[str, StatusSegment] = {}


def _import_attr(dotted: str):
    module_name, _, attr = dotted.partition(":")
    import importlib

    return getattr(importlib.import_module(module_name), attr)


def _normalize_ids(ids) -> List[str]:
    if isinstance(ids, str):
        ids = [ids]
    selected: List[str] = []
    for item in ids or ():
        if item not in _builtin_specs:
            raise KeyError(f"未知内置状态栏段: {item!r}（可用: {list(_builtin_specs)}）")
        selected.append(item)
    return selected


def builtin_segment_ids() -> list[str]:
    return list(_builtin_specs)


def default_segment(spec_id: str) -> StatusSegment:
    try:
        return _builtin_specs[spec_id]
    except KeyError:
        raise KeyError(f"未知内置状态栏段: {spec_id!r}（可用: {list(_builtin_specs)}）") from None


def active_segments() -> Dict[str, StatusSegment]:
    with _lock:
        result: Dict[str, StatusSegment] = {}
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


def active_segment_ids() -> list[str]:
    segments = active_segments()
    return sorted(segments, key=lambda sid: segments[sid].order)


def register_builtin_segment(spec_id: str, spec: StatusSegment = None) -> Callable[[], None]:
    if spec_id not in _builtin_specs:
        raise KeyError(f"未知内置状态栏段: {spec_id!r}（可用: {list(_builtin_specs)}）")
    with _lock:
        previous = _registered_builtin.get(spec_id, _ABSENT)
        _registered_builtin[spec_id] = spec if spec is not None else _builtin_specs[spec_id]

    def _undo() -> None:
        with _lock:
            if previous is _ABSENT:
                _registered_builtin.pop(spec_id, None)
            else:
                _registered_builtin[spec_id] = previous

    return _undo


def unregister_builtin_segment(spec_id: str) -> bool:
    with _lock:
        return _registered_builtin.pop(spec_id, None) is not None


def set_managed_builtin_segments(ids) -> Callable[[], None]:
    selected = _normalize_ids(ids)
    with _lock:
        added = [item for item in selected if item not in _managed_builtin]
        _managed_builtin.update(added)

    def _undo() -> None:
        with _lock:
            for item in added:
                _managed_builtin.discard(item)

    return _undo


def managed_segment_ids() -> list[str]:
    with _lock:
        return sorted(_managed_builtin)


def disable_builtin_segments(ids) -> Callable[[], None]:
    selected = _normalize_ids(ids)
    with _lock:
        added = [item for item in selected if item not in _disabled_builtin]
        _disabled_builtin.update(added)

    def _undo() -> None:
        with _lock:
            for item in added:
                _disabled_builtin.discard(item)

    return _undo


def register_segment(spec: StatusSegment) -> Callable[[], None]:
    if not isinstance(spec, StatusSegment):
        raise TypeError(f"扩展段必须是 StatusSegment: {spec!r}")
    with _lock:
        previous = _extension.get(spec.id, _ABSENT)
        _extension[spec.id] = spec

    def _undo() -> None:
        with _lock:
            if previous is _ABSENT:
                _extension.pop(spec.id, None)
            else:
                _extension[spec.id] = previous

    return _undo


def unregister_segment(spec_id: str) -> bool:
    with _lock:
        return _extension.pop(spec_id, None) is not None


def extension_segments() -> Dict[str, StatusSegment]:
    with _lock:
        return dict(_extension)


def resolve_segment(spec_id: str) -> Callable:
    """解析某段的实现（扩展优先 → 生效内置 → None）。"""
    with _lock:
        spec = _extension.get(spec_id)
        if spec is None:
            spec = active_segments().get(spec_id)
    if spec is None:
        return None
    return _import_attr(spec.handler)


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
    "StatusSegment",
    "builtin_segment_ids",
    "default_segment",
    "active_segments",
    "active_segment_ids",
    "register_builtin_segment",
    "unregister_builtin_segment",
    "set_managed_builtin_segments",
    "managed_segment_ids",
    "disable_builtin_segments",
    "register_segment",
    "unregister_segment",
    "extension_segments",
    "resolve_segment",
    "clear",
    "reset",
]
