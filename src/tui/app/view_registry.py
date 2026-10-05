"""TUI 视图注册表 — 模态全屏视图 / 模态底部视图的单一来源（一切皆插件）。

「一切皆插件」：TUI 视图（``trace`` / ``trace_tools`` / ``config`` / ``plugin``
全屏视图，``user_select`` / ``editmsg`` 底部视图）不再硬编码在 ``app.py`` 的
字典字面量里，而是注册到本注册表；每一项都由清单中的**独立插件条目**
（``ui_view``）显式声明，可被 Profile/Bundle 声明，也可被 Patch/Overlay 按 id
单独禁用、覆盖或替换。

**清单接管**：``ui`` 聚合插件收到组合根注入的 ``managed_ui_views``（清单已接管
的 id，含被禁用的）时经 ``set_managed_builtin_views`` 声明这些 id 由清单条目
负责；无清单（单元测试、独立调用）时无接管，全部内置视图默认生效。

``app.py`` 的 ``FULLSCREEN_VIEWS`` / ``BOTTOM_VIEWS`` 是本注册表导出的**同源
字典对象**（``fullscreen_views()`` / ``bottom_views()``），注册表变更后经
``_refresh()`` 原地更新，App 渲染分支无需改动。

视图规格（:class:`ViewSpec`）：``kind`` 为 ``fullscreen`` / ``bottom``；
``component`` 为可调用组件或点分引用；底部视图 ``key_mode`` 决定 fiber key
（``static`` 固定 key / ``user_select_seq`` / ``editmsg_seq`` 序号 key）。
"""

from __future__ import annotations

import importlib
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

_lock = threading.RLock()
_ABSENT = object()


@dataclass(frozen=True)
class ViewSpec:
    id: str
    kind: str
    component: Any  # 可调用组件 或 点分引用字符串
    key_mode: str = ""
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "component": self.component if isinstance(self.component, str) else getattr(self.component, "__name__", repr(self.component)),
            "key_mode": self.key_mode,
            "description": self.description,
        }


def _static_key(view_id: str) -> Callable[[Any], str]:
    return lambda model: f"bv-{view_id}"


def _user_select_key(view_id: str) -> Callable[[Any], str]:
    return lambda model: "us-popup"


def _editmsg_key(view_id: str) -> Callable[[Any], str]:
    def _key(model):
        state = getattr(model, "editmsg_select", None)
        return f"em-{getattr(state, 'seq', 0)}"

    return _key


_KEY_FUNCS: Dict[str, Callable[[str], Callable[[Any], str]]] = {
    "static": _static_key,
    "user_select_seq": _user_select_key,
    "editmsg_seq": _editmsg_key,
}

#: 内置视图声明
_BUILTIN_SPECS: tuple = (
    ViewSpec("trace", "fullscreen", "src.tui.app.trace_view.TraceView",
             description="轨迹视图（DSH 风格台账 + 检查器）"),
    ViewSpec("trace_tools", "fullscreen", "src.tui.app.trace_tools_view.TraceToolsView",
             description="轨迹工具列表详情视图"),
    ViewSpec("config", "fullscreen", "src.tui.app.config_view.ConfigView",
             description="配置中心视图"),
    ViewSpec("plugin", "fullscreen", "src.tui.app.plugin_view.PluginView",
             description="插件总览视图"),
    ViewSpec("user_select", "bottom", "src.tui.app.user_select.UserSelectPopup",
             key_mode="user_select_seq", description="user_select 弹窗（底部模态视图）"),
    ViewSpec("editmsg", "bottom", "src.tui.app.editmsg_select.EditMsgSelectPopup",
             key_mode="editmsg_seq", description="editmsg 消息选择弹窗（底部模态视图）"),
)

_builtin_specs: Dict[str, ViewSpec] = {spec.id: spec for spec in _BUILTIN_SPECS}
_registered_builtin: Dict[str, ViewSpec] = {}
_managed_builtin: set = set()
_disabled_builtin: set = set()
_extension: Dict[str, ViewSpec] = {}

#: app.py 引用的同源字典（原地更新，保持对象身份稳定）
_FULLSCREEN: Dict[str, Any] = {}
_BOTTOM: Dict[str, Any] = {}


def _normalize_ids(ids) -> List[str]:
    if isinstance(ids, str):
        ids = [ids]
    selected: List[str] = []
    for item in ids or ():
        if item not in _builtin_specs:
            raise KeyError(f"未知内置 UI 视图: {item!r}（可用: {list(_builtin_specs)}）")
        selected.append(item)
    return selected


def _resolve_component(ref: Any) -> Any:
    if callable(ref):
        return ref
    module_name, _, attr = str(ref).rpartition(".")
    module = importlib.import_module(module_name)
    return getattr(module, attr)


def _active_specs() -> Dict[str, ViewSpec]:
    result: Dict[str, ViewSpec] = {}
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
    result.update(_extension)
    return result


def _refresh() -> None:
    fullscreen: Dict[str, Any] = {}
    bottom: Dict[str, Any] = {}
    for spec in _active_specs().values():
        try:
            component = _resolve_component(spec.component)
        except Exception:
            continue
        if spec.kind == "fullscreen":
            fullscreen[spec.id] = component
        elif spec.kind == "bottom":
            if spec.key_mode and spec.key_mode != "static":
                key_fn = _KEY_FUNCS.get(spec.key_mode, _static_key)(spec.id)
                bottom[spec.id] = (component, key_fn)
            else:
                bottom[spec.id] = component
    _FULLSCREEN.clear()
    _FULLSCREEN.update(fullscreen)
    _BOTTOM.clear()
    _BOTTOM.update(bottom)


def fullscreen_views() -> dict:
    """全屏视图字典（与 ``app.FULLSCREEN_VIEWS`` 同源对象）。"""
    with _lock:
        if not _FULLSCREEN and not _disabled_builtin and not _managed_builtin:
            _refresh()
        return _FULLSCREEN


def bottom_views() -> dict:
    """底部视图字典（与 ``app.BOTTOM_VIEWS`` 同源对象）。"""
    with _lock:
        if not _BOTTOM and not _disabled_builtin and not _managed_builtin:
            _refresh()
        return _BOTTOM


def builtin_view_ids() -> list[str]:
    return list(_builtin_specs)


def default_view_spec(spec_id: str) -> ViewSpec:
    try:
        return _builtin_specs[spec_id]
    except KeyError:
        raise KeyError(f"未知内置 UI 视图: {spec_id!r}（可用: {list(_builtin_specs)}）") from None


def active_view_ids() -> list[str]:
    with _lock:
        return sorted(_active_specs())


def describe() -> list:
    with _lock:
        return [spec.to_dict() for spec in _active_specs().values()]


def register_builtin_view(spec_id: str, spec: Optional[ViewSpec] = None) -> Callable[[], None]:
    """注册/覆盖一个内置视图（``spec=None`` 用默认声明）；返回幂等撤销。"""
    if spec_id not in _builtin_specs:
        raise KeyError(f"未知内置 UI 视图: {spec_id!r}（可用: {list(_builtin_specs)}）")
    with _lock:
        previous = _registered_builtin.get(spec_id, _ABSENT)
        _registered_builtin[spec_id] = spec if spec is not None else _builtin_specs[spec_id]
        _refresh()

    def _undo() -> None:
        with _lock:
            if previous is _ABSENT:
                _registered_builtin.pop(spec_id, None)
            else:
                _registered_builtin[spec_id] = previous
            _refresh()

    return _undo


def unregister_builtin_view(spec_id: str) -> bool:
    with _lock:
        removed = _registered_builtin.pop(spec_id, None) is not None
        _refresh()
        return removed


def register_view(spec: ViewSpec) -> Callable[[], None]:
    """注册一个扩展视图（返回幂等撤销）。"""
    if not isinstance(spec, ViewSpec) or not spec.id:
        raise TypeError(f"视图规格非法: {spec!r}")
    with _lock:
        previous = _extension.get(spec.id, _ABSENT)
        _extension[spec.id] = spec
        _refresh()

    def _undo() -> None:
        with _lock:
            if previous is _ABSENT:
                _extension.pop(spec.id, None)
            else:
                _extension[spec.id] = previous
            _refresh()

    return _undo


def unregister_view(spec_id: str) -> bool:
    with _lock:
        removed = _extension.pop(spec_id, None) is not None
        _refresh()
        return removed


def set_managed_builtin_views(ids) -> Callable[[], None]:
    selected = _normalize_ids(ids)
    with _lock:
        added = [item for item in selected if item not in _managed_builtin]
        _managed_builtin.update(added)
        _refresh()

    def _undo() -> None:
        with _lock:
            for item in added:
                _managed_builtin.discard(item)
            _refresh()

    return _undo


def managed_view_ids() -> list[str]:
    with _lock:
        return sorted(_managed_builtin)


def disable_builtin_views(ids) -> Callable[[], None]:
    selected = _normalize_ids(ids)
    with _lock:
        added = [item for item in selected if item not in _disabled_builtin]
        _disabled_builtin.update(added)
        _refresh()

    def _undo() -> None:
        with _lock:
            for item in added:
                _disabled_builtin.discard(item)
            _refresh()

    return _undo


def clear() -> None:
    with _lock:
        _extension.clear()
        _registered_builtin.clear()
        _refresh()


def reset() -> None:
    with _lock:
        _extension.clear()
        _registered_builtin.clear()
        _managed_builtin.clear()
        _disabled_builtin.clear()
        _refresh()


__all__ = [
    "ViewSpec",
    "fullscreen_views",
    "bottom_views",
    "builtin_view_ids",
    "default_view_spec",
    "active_view_ids",
    "describe",
    "register_builtin_view",
    "unregister_builtin_view",
    "register_view",
    "unregister_view",
    "set_managed_builtin_views",
    "managed_view_ids",
    "disable_builtin_views",
    "clear",
    "reset",
]
