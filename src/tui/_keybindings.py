"""键位绑定注册表 — Ctrl 组合键 → action 的单一来源（一切皆插件）。

「一切皆插件」：TUI 输入分发（``InputDispatcher._handle_ctrl_key``）的 Ctrl
组合键绑定不再是硬编码的 ``if ch == "\\x07"`` 分支链，而是注册到本模块的
规格表；每一个内置绑定都由清单中的**独立插件条目**（``keybinding``）显式
注册，因而可被 Profile/Bundle 声明，也可被 Patch/Overlay 按 id 单独禁用、
覆盖（改键 / 换 action）或替换。

**清单接管**：``keybindings`` 聚合插件收到组合根注入的 ``managed_keybindings``
（清单已接管的 id，含被禁用的）时经 ``set_managed_builtin_keybindings`` 声明
这些 id 由清单条目负责——对应内置项不再走默认装配；被禁用（未挂载）的条目
因此真正缺席。无清单（单元测试、独立调用）时无接管，全部内置项默认生效。

绑定解析按「扩展 → 生效内置」顺序取该键的第一个匹配项；无匹配返回 ``None``
（未知组合键 no-op）。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

_lock = threading.RLock()
_ABSENT = object()


@dataclass(frozen=True)
class KeyBinding:
    """一个键位绑定规格：控制键 → 分发动作。"""

    id: str
    key: str
    action: str
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "key": self.key,
            "action": self.action,
            "description": self.description,
        }


#: 内置绑定声明（声明顺序即展示顺序）
_BUILTIN_SPECS: Tuple[KeyBinding, ...] = (
    KeyBinding("ctrl_g", "\x07", "vim", "Ctrl+G 打开 vim 编辑"),
    KeyBinding("ctrl_o", "\x0f", "editmsg", "Ctrl+O 编辑消息"),
    KeyBinding("ctrl_h", "\x08", "trace_toggle", "Ctrl+H 轨迹视图开关"),
    KeyBinding("ctrl_r", "\x12", "ctrl_r", "Ctrl+R 反向历史搜索 / 重新生成上一轮"),
    KeyBinding("ctrl_l", "\x0c", "clear_screen", "Ctrl+L 清屏"),
    KeyBinding("ctrl_d", "\x04", "ctrl_d", "Ctrl+D EOF（空缓冲提交 exit）"),
    KeyBinding("ctrl_t", "\x14", "toggle_theme", "Ctrl+T 主题切换"),
    KeyBinding("ctrl_n", "\x0e", "switch_model", "Ctrl+N 切换模型"),
    KeyBinding("ctrl_p", "\x10", "history_prev", "Ctrl+P 历史上一条"),
    KeyBinding("ctrl_b", "\x02", "cycle_mode", "Ctrl+B 主 Agent 运行模式循环"),
)

_builtin_specs: Dict[str, KeyBinding] = {spec.id: spec for spec in _BUILTIN_SPECS}

#: 由清单条目注册/覆盖的内置绑定（id → 规格）
_registered_builtin: Dict[str, KeyBinding] = {}
#: 由清单接管的内置绑定 id（默认装配被抑制）
_managed_builtin: set = set()
#: 显式禁用的内置绑定 id
_disabled_builtin: set = set()
#: 扩展绑定（id → 规格）
_extension: Dict[str, KeyBinding] = {}


def _normalize_ids(ids) -> List[str]:
    if isinstance(ids, str):
        ids = [ids]
    selected: List[str] = []
    for item in ids or ():
        if item not in _builtin_specs:
            raise KeyError(f"未知内置键位绑定: {item!r}（可用: {list(_builtin_specs)}）")
        selected.append(item)
    return selected


def builtin_keybinding_ids() -> list[str]:
    """全部内置绑定 id（按声明顺序）。"""
    return list(_builtin_specs)


def default_keybinding(spec_id: str) -> KeyBinding:
    try:
        return _builtin_specs[spec_id]
    except KeyError:
        raise KeyError(f"未知内置键位绑定: {spec_id!r}（可用: {list(_builtin_specs)}）") from None


def active_keybindings() -> Dict[str, KeyBinding]:
    """当前生效的内置绑定（``id → 规格``；按声明顺序）。

    规则：显式禁用 → 跳过；条目注册/覆盖 → 用注册规格；清单接管且无注册 →
    跳过（由条目负责）；否则 → 默认规格。
    """
    with _lock:
        result: Dict[str, KeyBinding] = {}
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


def register_builtin_keybinding(spec_id: str, spec: Optional[KeyBinding] = None) -> Callable[[], None]:
    """注册/覆盖一个内置绑定（``spec=None`` 用默认规格）；返回幂等撤销。"""
    if spec_id not in _builtin_specs:
        raise KeyError(f"未知内置键位绑定: {spec_id!r}（可用: {list(_builtin_specs)}）")
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


def unregister_builtin_keybinding(spec_id: str) -> bool:
    with _lock:
        return _registered_builtin.pop(spec_id, None) is not None


def set_managed_builtin_keybindings(ids) -> Callable[[], None]:
    """声明这些内置绑定 id 由清单条目负责（默认装配被抑制）；返回撤销。"""
    selected = _normalize_ids(ids)
    with _lock:
        added = [item for item in selected if item not in _managed_builtin]
        _managed_builtin.update(added)

    def _undo() -> None:
        with _lock:
            for item in added:
                _managed_builtin.discard(item)

    return _undo


def managed_keybinding_ids() -> list[str]:
    with _lock:
        return sorted(_managed_builtin)


def disable_builtin_keybindings(ids) -> Callable[[], None]:
    """禁用一个或多个内置绑定（返回幂等撤销）。"""
    selected = _normalize_ids(ids)
    with _lock:
        added = [item for item in selected if item not in _disabled_builtin]
        _disabled_builtin.update(added)

    def _undo() -> None:
        with _lock:
            for item in added:
                _disabled_builtin.discard(item)

    return _undo


def register_keybinding(spec: KeyBinding) -> Callable[[], None]:
    """注册一个扩展绑定（id 覆盖内置 / 新键）；返回幂等撤销。"""
    if not isinstance(spec, KeyBinding):
        raise TypeError(f"扩展绑定必须是 KeyBinding: {spec!r}")
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


def unregister_keybinding(spec_id: str) -> bool:
    with _lock:
        return _extension.pop(spec_id, None) is not None


def extension_keybindings() -> Dict[str, KeyBinding]:
    with _lock:
        return dict(_extension)


def resolve_binding(key: str) -> Optional[str]:
    """解析按键对应的分发动作（扩展优先 → 生效内置；无匹配返回 None）。"""
    with _lock:
        for spec in _extension.values():
            if spec.key == key:
                return spec.action
        for spec in active_keybindings().values():
            if spec.key == key:
                return spec.action
    return None


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
    "KeyBinding",
    "builtin_keybinding_ids",
    "default_keybinding",
    "active_keybindings",
    "register_builtin_keybinding",
    "unregister_builtin_keybinding",
    "set_managed_builtin_keybindings",
    "managed_keybinding_ids",
    "disable_builtin_keybindings",
    "register_keybinding",
    "unregister_keybinding",
    "extension_keybindings",
    "resolve_binding",
    "clear",
    "reset",
]
