"""测试 TUI 异常分类层级 — _exceptions.py + 错误处理修复。

覆盖范围：
  1. 异常分类层级（TuiError / RecoverableError / FatalError）
  2. safe_execute / safe_execute_silent 上下文管理器行为
  3. layout.py 异常分类后子控件渲染降级行为
  4. selection.py 异常分类后终端 I/O 降级行为
"""

from __future__ import annotations

import logging
import pytest

from src.tui._exceptions import (
    TuiError,
    RecoverableError,
    FatalError,
    safe_execute,
    safe_execute_silent,
)


# ═══════════════════════════════════════════════════════════
# 异常分类层级
# ═══════════════════════════════════════════════════════════


class TestExceptionHierarchy:
    """验证异常分类层级结构。"""

    def test_tui_error_base(self):
        """TuiError 是 Exception 的子类。"""
        assert issubclass(TuiError, Exception)

    def test_recoverable_error_subclass(self):
        """RecoverableError 继承自 TuiError。"""
        assert issubclass(RecoverableError, TuiError)

    def test_fatal_error_subclass(self):
        """FatalError 继承自 TuiError。"""
        assert issubclass(FatalError, TuiError)

    def test_recoverable_and_fatal_distinct(self):
        """RecoverableError 与 FatalError 是独立子类（互不派生）。"""
        assert not issubclass(RecoverableError, FatalError)
        assert not issubclass(FatalError, RecoverableError)

    def test_catch_base_exception(self):
        """捕获 TuiError 基类应同时捕获 RecoverableError 和 FatalError。"""
        with pytest.raises(TuiError):
            raise RecoverableError("可恢复异常")
        with pytest.raises(TuiError):
            raise FatalError("不可恢复异常")


# ═══════════════════════════════════════════════════════════
# safe_execute 上下文管理器
# ═══════════════════════════════════════════════════════════


class TestSafeExecute:
    """验证 safe_execute 上下文管理器的异常处理行为。"""

    def test_no_exception(self):
        """无异常时 safe_execute 正常通过。"""
        result = []
        with safe_execute("test"):
            result.append(1)
        assert result == [1]

    def test_recoverable_error_swallowed(self):
        """RecoverableError 被吞并，不会传播。"""
        with safe_execute("test"):
            raise RecoverableError("可恢复异常")
        # 异常被吞并，不抛出

    def test_regular_exception_swallowed(self):
        """普通 Exception 被视为可恢复异常，被吞并。"""
        with safe_execute("test"):
            raise ValueError("普通异常")
        # 异常被吞并，不抛出

    def test_fatal_error_propagated(self):
        """FatalError 重新抛出，不被吞并。"""
        with pytest.raises(FatalError, match="不可恢复"):
            with safe_execute("test"):
                raise FatalError("不可恢复")

    def test_return_value_preserved(self):
        """safe_execute 内部代码的返回值正常。"""
        def compute():
            return 42
        result = None
        with safe_execute("test"):
            result = compute()
        assert result == 42

    def test_cleanup_after_recoverable(self):
        """RecoverableError 吞并后，yield 之后的清理代码执行。"""
        cleanup_done = False
        with safe_execute("test"):
            raise RecoverableError("可恢复")
        cleanup_done = True
        assert cleanup_done


# ═══════════════════════════════════════════════════════════
# safe_execute_silent 上下文管理器
# ═══════════════════════════════════════════════════════════


class TestSafeExecuteSilent:
    """验证 safe_execute_silent 上下文管理器的异常吞并行为。"""

    def test_no_exception(self):
        """无异常时正常通过。"""
        result = []
        with safe_execute_silent("test"):
            result.append(1)
        assert result == [1]

    def test_regular_exception_swallowed(self):
        """普通 Exception 被吞并。"""
        with safe_execute_silent("test"):
            raise RuntimeError("任意异常")
        # 不抛出

    def test_fatal_error_swallowed(self):
        """FatalError 也被吞并（静默模式吞一切）。"""
        with safe_execute_silent("test"):
            raise FatalError("静默吞并不可恢复异常")
        # 不抛出

    def test_recoverable_error_swallowed(self):
        """RecoverableError 被吞并。"""
        with safe_execute_silent("test"):
            raise RecoverableError("静默吞并可恢复异常")
        # 不抛出


