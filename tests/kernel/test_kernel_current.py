"""进程级当前内核注册表测试 — src.kernel.current（无 kernel↔fiber 循环）。

覆盖：
- set/get/current_context 的基本语义；
- activate_kernel 的嵌套激活与恢复；
- 当前登记被外部改写后，恢复函数不覆盖外部登记（嵌套安全）。
"""

from __future__ import annotations

from src.kernel.current import (
    activate_kernel,
    current_context,
    get_current_kernel,
    set_current_kernel,
)
from src.kernel.kernel import Kernel


def test_set_and_get_roundtrip():
    previous = get_current_kernel()
    try:
        kernel = Kernel(name="current-test")
        set_current_kernel(kernel)
        assert get_current_kernel() is kernel
        assert current_context() is kernel.root
    finally:
        set_current_kernel(previous)


def test_current_context_none_without_kernel():
    previous = get_current_kernel()
    try:
        set_current_kernel(None)
        assert get_current_kernel() is None
        assert current_context() is None
    finally:
        set_current_kernel(previous)


def test_activate_nested_restore():
    previous = get_current_kernel()
    try:
        set_current_kernel(None)
        outer = Kernel(name="outer")
        inner = Kernel(name="inner")

        restore_outer = activate_kernel(outer)
        assert get_current_kernel() is outer

        restore_inner = activate_kernel(inner)
        assert get_current_kernel() is inner

        restore_inner()
        assert get_current_kernel() is outer

        restore_outer()
        assert get_current_kernel() is None
    finally:
        set_current_kernel(previous)


def test_restore_does_not_clobber_external_registration():
    previous = get_current_kernel()
    try:
        set_current_kernel(None)
        first = Kernel(name="first")
        second = Kernel(name="second")

        restore = activate_kernel(first)
        set_current_kernel(second)
        restore()
        assert get_current_kernel() is second
    finally:
        set_current_kernel(previous)


def test_kernel_module_reexports_current_api():
    from src.kernel import kernel as kernel_mod

    assert kernel_mod.set_current_kernel is set_current_kernel
    assert kernel_mod.get_current_kernel is get_current_kernel
    assert kernel_mod.current_context is current_context
    assert kernel_mod._activate_kernel is activate_kernel
