"""通用声明式注册表 — 「一切皆插件」的共享原语。

dsh 的插件树要求每个内置项都是清单中的独立条目：可被 Profile/Bundle 声明，
也可被 Patch/Overlay 按 id 覆盖、禁用或替换。各领域注册表此前各自重复实现
「内置声明 + 清单接管 + 覆盖 + 扩展」四段逻辑；本模块把该模式收敛为一处，
供工具元数据 / 工具常量 / 事件类型等注册表复用。

两个原语：

- :class:`DeclarativeRegistry`：``id → 值`` 声明式注册表；
- :class:`LiveSequence`：实时委托注册表的只读序列视图（保留「模块级元组常量」
  调用面，如 ``x in ALL`` / ``for x in ALL``）。

语义（与既有各领域注册表一致）：

- **内置声明**：``declare`` 登记 ``id → 默认值``（声明顺序即装配顺序）；
- **清单接管**：``set_managed`` 声明某些 id 由清单条目负责——默认装配被抑制，
  条目挂载时经 ``register_builtin`` 注册（被禁用则缺席）；
- **覆盖**：``register_builtin`` 用新值替换默认值（可撤销）；
- **禁用**：``disable_builtin`` 令默认值缺席（可撤销）；
- **扩展**：``register_extension`` 追加外部项（不受内置约束）。

本模块为叶子模块（仅标准库），任何层均可引用。
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, Iterable, List

_ABSENT = object()


class DeclarativeRegistry:
    """``id → 值`` 声明式注册表（线程安全）。"""

    def __init__(self, label: str = "条目") -> None:
        self._label = label
        self._builtins: Dict[str, Any] = {}
        self._registered: Dict[str, Any] = {}
        self._managed: set = set()
        self._disabled: set = set()
        self._extension: Dict[str, Any] = {}
        self._lock = threading.RLock()

    # ── 内置声明 ──────────────────────────────────────────

    def declare(self, specs) -> None:
        """登记内置声明（``{id: value}`` 或 ``[(id, value), ...]``）。"""
        items = specs.items() if isinstance(specs, dict) else specs
        with self._lock:
            for spec_id, value in items:
                self._builtins.setdefault(str(spec_id), value)

    def builtin_ids(self) -> List[str]:
        with self._lock:
            return list(self._builtins)

    def default(self, spec_id: str) -> Any:
        with self._lock:
            try:
                return self._builtins[spec_id]
            except KeyError:
                raise KeyError(
                    f"未知内置{self._label}: {spec_id!r}（可用: {list(self._builtins)}）"
                ) from None

    # ── 生效项 ────────────────────────────────────────────

    def active(self) -> Dict[str, Any]:
        """当前生效项（``id → 值``；按声明顺序，扩展项追加在后）。"""
        with self._lock:
            result: Dict[str, Any] = {}
            for spec_id, default in self._builtins.items():
                if spec_id in self._disabled:
                    continue
                override = self._registered.get(spec_id, _ABSENT)
                if override is not _ABSENT:
                    result[spec_id] = override
                    continue
                if spec_id in self._managed:
                    continue
                result[spec_id] = default
            result.update(self._extension)
            return result

    def value(self, spec_id: str, default: Any = None) -> Any:
        """按 id 取当前生效值（缺席返回 ``default``）。"""
        return self.active().get(spec_id, default)

    def describe(self) -> List[dict]:
        with self._lock:
            active = self.active()
            return [
                {
                    "id": spec_id,
                    "active": spec_id in active,
                    "managed": spec_id in self._managed,
                    "disabled": spec_id in self._disabled,
                    "registered": spec_id in self._registered,
                }
                for spec_id in list(self._builtins) + [
                    key for key in self._extension if key not in self._builtins
                ]
            ]

    # ── 注册 / 撤销 ───────────────────────────────────────

    def _normalize(self, ids) -> List[str]:
        if isinstance(ids, str):
            ids = [ids]
        selected: List[str] = []
        for item in ids or ():
            if item not in self._builtins:
                raise KeyError(
                    f"未知内置{self._label}: {item!r}（可用: {list(self._builtins)}）"
                )
            selected.append(item)
        return selected

    def register_builtin(self, spec_id: str, value: Any = None) -> Callable[[], None]:
        """用 ``value``（None → 默认值）注册/覆盖内置项；返回幂等撤销。"""
        if spec_id not in self._builtins:
            raise KeyError(
                f"未知内置{self._label}: {spec_id!r}（可用: {list(self._builtins)}）"
            )
        effective = self._builtins[spec_id] if value is None else value
        with self._lock:
            previous = self._registered.get(spec_id, _ABSENT)
            self._registered[spec_id] = effective

        def _undo() -> None:
            with self._lock:
                if previous is _ABSENT:
                    self._registered.pop(spec_id, None)
                else:
                    self._registered[spec_id] = previous

        return _undo

    def unregister_builtin(self, spec_id: str) -> bool:
        with self._lock:
            return self._registered.pop(spec_id, None) is not None

    def register_extension(self, spec_id: str, value: Any) -> Callable[[], None]:
        """注册扩展项（不受内置约束）；返回幂等撤销。"""
        with self._lock:
            previous = self._extension.get(spec_id, _ABSENT)
            self._extension[spec_id] = value

        def _undo() -> None:
            with self._lock:
                if previous is _ABSENT:
                    self._extension.pop(spec_id, None)
                else:
                    self._extension[spec_id] = previous

        return _undo

    def unregister_extension(self, spec_id: str) -> bool:
        with self._lock:
            return self._extension.pop(spec_id, None) is not None

    # ── 清单接管 / 禁用 ───────────────────────────────────

    def set_managed(self, ids) -> Callable[[], None]:
        selected = self._normalize(ids)
        with self._lock:
            added = [item for item in selected if item not in self._managed]
            self._managed.update(added)

        def _undo() -> None:
            with self._lock:
                for item in added:
                    self._managed.discard(item)

        return _undo

    def managed_ids(self) -> List[str]:
        with self._lock:
            return sorted(self._managed)

    def disable_builtin(self, ids) -> Callable[[], None]:
        selected = self._normalize(ids)
        with self._lock:
            added = [item for item in selected if item not in self._disabled]
            self._disabled.update(added)

        def _undo() -> None:
            with self._lock:
                for item in added:
                    self._disabled.discard(item)

        return _undo

    def disabled_ids(self) -> List[str]:
        with self._lock:
            return sorted(self._disabled)

    # ── 清理 ──────────────────────────────────────────────

    def clear(self) -> None:
        with self._lock:
            self._extension.clear()
            self._registered.clear()

    def reset(self) -> None:
        with self._lock:
            self._extension.clear()
            self._registered.clear()
            self._managed.clear()
            self._disabled.clear()


class LiveSequence:
    """只读实时序列视图 — 每次访问委托 ``provider()`` 取当前序列。

    供消费方以「模块级元组常量」形态保留旧调用面（``in`` / 迭代 / ``len`` /
    下标），同时底层数据取自注册表当前生效项。
    """

    __slots__ = ("_provider",)

    def __init__(self, provider: Callable[[], Iterable]) -> None:
        self._provider = provider

    def _seq(self) -> tuple:
        return tuple(self._provider())

    def __iter__(self):
        return iter(self._seq())

    def __len__(self) -> int:
        return len(self._seq())

    def __contains__(self, item) -> bool:
        return item in self._seq()

    def __getitem__(self, index):
        return self._seq()[index]

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return repr(self._seq())

    def __eq__(self, other) -> bool:
        if isinstance(other, LiveSequence):
            return self._seq() == other._seq()
        return self._seq() == tuple(other)

    def __hash__(self) -> int:
        return hash(self._seq())

    def index(self, item) -> int:
        return self._seq().index(item)

    def count(self, item) -> int:
        return self._seq().count(item)


__all__ = ["DeclarativeRegistry", "LiveSequence"]
