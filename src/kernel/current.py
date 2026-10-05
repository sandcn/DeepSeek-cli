"""进程级当前内核注册表 — 组合根写入、运行时组件读取。

「一切皆插件」：内核实例是**进程级组合根**产物的访问点。本模块只持有
当前内核的弱引用式登记与激活语义，不依赖 ``Kernel`` / ``Fiber`` / ``Context``
等具体类型，因此可被内核自身的任意模块（含 ``fiber``）安全引用，避免
``kernel ↔ fiber`` 循环依赖。

- ``set_current_kernel`` / ``get_current_kernel``：组合根登记 / 读取；
- ``current_context``：当前内核的根上下文（无内核时 None）；
- ``activate_kernel``：插件构造期临时把内核登记为当前内核，返回恢复函数
  （嵌套安全），供 ``Fiber`` 在 apply 期间保证进程级单例与内核服务同源。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

_current: Any = None


def set_current_kernel(kernel: Any) -> None:
    global _current
    _current = kernel


def get_current_kernel() -> Any:
    return _current


def current_context() -> Optional[Any]:
    kernel = _current
    return kernel.root if kernel is not None else None


def activate_kernel(kernel: Any) -> Callable[[], None]:
    """插件构造期把内核临时登记为进程级当前内核，返回恢复函数。

    插件 apply 中经 ``active_service`` 解析到的进程级单例必须与内核服务同源
    （内核服务是唯一真源）；构造期内核尚未被组合根登记为当前内核，若不激活
    则单例会回退到游离的模块级默认实例，导致运行时不变量误报。恢复函数具备
    嵌套安全：仅当当前登记仍指向本次内核时才回退到进入前的登记。
    """
    global _current
    previous = _current
    _current = kernel

    def _restore() -> None:
        global _current
        if _current is kernel:
            _current = previous

    return _restore


__all__ = [
    "set_current_kernel",
    "get_current_kernel",
    "current_context",
    "activate_kernel",
]
