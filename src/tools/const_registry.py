"""工具常量注册表 — 每个内置常量一条独立声明（一切皆插件）。

「一切皆插件」：工具运行期常量（排除目录/文件模式、路径安全集合、编码检测
常量、文件大小阈值、图片扩展名、网页移除标签等）不再是各模块的模块级字面量，
而是注册到本模块的声明表；每个常量由清单中的**独立插件条目**（``tool_const``，
经 ``src.plugins.tool_const_entries``）注册，因而可被 Profile/Bundle 声明，
也可被 Patch/Overlay 按 id 单独禁用、覆盖或替换。

**清单接管**：``tool_consts`` 聚合插件收到组合根注入的 ``managed_tool_consts``
（清单已接管的常量名，含被禁用的）时经 ``set_managed_builtin_constants`` 声明
这些常量由清单条目负责——对应内置声明不再走默认装配；被禁用（未挂载）的条目
因此真正缺席（``const`` 返回调用方默认值）。无清单（单元测试、独立调用）时
无接管，全部内置声明默认生效。

消费方经 :func:`const` 实时查询（``src.tools._constants`` 的访问器/派生函数
均委托本注册表）。
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List

from ..declarative import DeclarativeRegistry

_REGISTRY = DeclarativeRegistry("工具常量")

_gen_lock = threading.RLock()
_generation = 0
_active_cache: Dict[str, Any] = {"gen": -1, "data": {}}


def generation() -> int:
    with _gen_lock:
        return _generation


def _bump() -> None:
    global _generation
    with _gen_lock:
        _generation += 1


def _wrapped(undo: Callable[[], None]) -> Callable[[], None]:
    def _undo() -> None:
        undo()
        _bump()

    return _undo


# ── 内置声明 ─────────────────────────────────────────


def declare_constants(specs: Dict[str, Any]) -> None:
    """登记内置常量声明（``{名称: 值}``；首次声明为准）。"""
    _REGISTRY.declare(specs)
    _bump()


def builtin_constant_names() -> List[str]:
    return list(_REGISTRY.builtin_ids())


def default_constant(name: str) -> Any:
    return _REGISTRY.default(name)


def _active() -> Dict[str, Any]:
    global _active_cache
    gen = generation()
    if _active_cache.get("gen") != gen:
        _active_cache = {"gen": gen, "data": _REGISTRY.active()}
    return _active_cache["data"]


def active_constants() -> Dict[str, Any]:
    """当前生效的常量（``名称 → 值``）。"""
    return dict(_active())


def const(name: str, default: Any = None) -> Any:
    """按名称取当前生效常量值（缺席返回 ``default``）。"""
    return _active().get(name, default)


# ── 注册 / 撤销 ───────────────────────────────────────


def register_builtin_constant(name: str, value: Any = None) -> Callable[[], None]:
    """注册/覆盖内置常量（``value=None`` 用默认声明）；返回幂等撤销。"""
    undo = _REGISTRY.register_builtin(name, value)
    _bump()
    return _wrapped(undo)


def unregister_builtin_constant(name: str) -> bool:
    if _REGISTRY.unregister_builtin(name):
        _bump()
        return True
    return False


def register_constant(name: str, value: Any) -> Callable[[], None]:
    """注册扩展常量（不受内置约束）；返回幂等撤销。"""
    undo = _REGISTRY.register_extension(name, value)
    _bump()
    return _wrapped(undo)


def unregister_constant(name: str) -> bool:
    if _REGISTRY.unregister_extension(name):
        _bump()
        return True
    return False


# ── 清单接管 / 禁用 ───────────────────────────────────


def set_managed_builtin_constants(names) -> Callable[[], None]:
    undo = _REGISTRY.set_managed(names)
    _bump()
    return _wrapped(undo)


def managed_constant_names() -> List[str]:
    return list(_REGISTRY.managed_ids())


def disable_builtin_constants(names) -> Callable[[], None]:
    undo = _REGISTRY.disable_builtin(names)
    _bump()
    return _wrapped(undo)


def describe() -> List[dict]:
    return _REGISTRY.describe()


def clear() -> None:
    _REGISTRY.clear()
    _bump()


def reset() -> None:
    _REGISTRY.reset()
    _bump()


__all__ = [
    "generation",
    "declare_constants",
    "builtin_constant_names",
    "default_constant",
    "active_constants",
    "const",
    "register_builtin_constant",
    "unregister_builtin_constant",
    "register_constant",
    "unregister_constant",
    "set_managed_builtin_constants",
    "managed_constant_names",
    "disable_builtin_constants",
    "describe",
    "clear",
    "reset",
]
