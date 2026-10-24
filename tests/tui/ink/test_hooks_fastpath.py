"""hooks 上下文快速路径（2026-10-24）。

``_current`` / ``_push_current`` / ``_pop_current`` 改为直接读当前
``HookContext`` 的 fiber 栈引用（不再经 ``hooks`` 模块 PEP 562 代理）。
本测试锁定语义：多上下文切换、push/pop、渲染期外调用报错。
"""

from __future__ import annotations

import pytest

from src.tui.ink._hook_context import (
    HookContext,
    current_fiber_stack,
    pop_context,
    push_context,
)
from src.tui.ink._hooks_core import (
    HookStateError,
    _current,
    _pop_current,
    _push_current,
)


def test_current_follows_active_context() -> None:
    ctx = HookContext()
    first = object()
    push_context(ctx)
    try:
        _push_current(first)
        assert _current() is first
        _pop_current()
        with pytest.raises(HookStateError):
            _current()
    finally:
        pop_context()


def test_nested_context_restores_stack_reference() -> None:
    outer = HookContext()
    inner = HookContext()
    outer_fiber = object()
    inner_fiber = object()

    push_context(outer)
    try:
        _push_current(outer_fiber)
        push_context(inner)
        try:
            _push_current(inner_fiber)
            assert _current() is inner_fiber
            assert current_fiber_stack() is inner.current_fiber_stack
        finally:
            _pop_current()
            pop_context()
        # 恢复外层上下文后，栈引用与外层 fiber 均正确。
        assert current_fiber_stack() is outer.current_fiber_stack
        assert _current() is outer_fiber
    finally:
        _pop_current()
        pop_context()


def test_current_fiber_stack_tracks_restore() -> None:
    """``HookContext.restore`` 替换栈对象后，快速路径缓存必须同步。"""
    ctx = HookContext()
    push_context(ctx)
    try:
        saved = ctx.snapshot()
        replacement = ["fiber"]
        saved["current_fiber_stack"] = replacement
        ctx.restore(saved)
        assert current_fiber_stack() is replacement
    finally:
        pop_context()
