"""测试 src/core/telemetry/trace_context.py 的 TraceContext 和相关函数。"""

import re

import pytest

from src.core.telemetry.trace_context import (
    TraceContext,
    generate_span_id,
    generate_trace_id,
    get_current_span_id,
    get_current_trace_id,
    get_thread_trace_id,
    set_current_span_id,
    set_current_trace_id,
    set_thread_trace_id,
)


class TestGenerateTraceId:
    """generate_trace_id 格式验证"""

    TRACE_ID_PATTERN = re.compile(r"^trace_[0-9a-f]{12}$")

    def test_format(self):
        """格式为 trace_ + 12位 hex"""
        tid = generate_trace_id()
        assert self.TRACE_ID_PATTERN.match(tid), f"格式不匹配: {tid}"

    def test_uniqueness(self):
        """多次调用生成不同的 trace_id"""
        ids = {generate_trace_id() for _ in range(100)}
        assert len(ids) == 100

    def test_length(self):
        """总长度为 len("trace_") + 12 = 18"""
        tid = generate_trace_id()
        assert len(tid) == 18


class TestGenerateSpanId:
    """generate_span_id 格式验证"""

    SPAN_ID_PATTERN = re.compile(r"^span_[0-9a-f]{8}$")

    def test_format(self):
        """格式为 span_ + 8位 hex"""
        sid = generate_span_id()
        assert self.SPAN_ID_PATTERN.match(sid), f"格式不匹配: {sid}"

    def test_uniqueness(self):
        """多次调用生成不同的 span_id"""
        ids = {generate_span_id() for _ in range(100)}
        assert len(ids) == 100


class TestTraceContext:
    """TraceContext 功能测试"""

    def test_default_trace_id_not_empty(self):
        """不传参时自动生成非空 trace_id"""
        ctx = TraceContext()
        assert ctx.trace_id
        assert ctx.trace_id.startswith("trace_")

    def test_default_span_id_not_empty(self):
        """默认 span_id 非空"""
        ctx = TraceContext()
        assert ctx.span_id
        assert ctx.span_id.startswith("span_")

    def test_provided_trace_id(self):
        """传入 trace_id 应保留"""
        custom_id = "trace_custom123456"
        ctx = TraceContext(trace_id=custom_id)
        assert ctx.trace_id == custom_id

    # ── 上下文管理器 ──────────────────────────────────

    def test_context_manager_sets_values(self):
        """进入上下文后 get_current_trace_id / get_current_span_id 返回对应值"""
        ctx = TraceContext()
        with ctx:
            assert get_current_trace_id() == ctx.trace_id
            assert get_current_span_id() == ctx.span_id

    def test_context_manager_restores_after_exit(self):
        """退出上下文后恢复之前的上下文变量"""
        # 先设置一个上下文
        set_current_trace_id("outer_trace")
        set_current_span_id("outer_span")

        ctx = TraceContext(trace_id="inner_trace")
        with ctx:
            assert get_current_trace_id() == "inner_trace"

        # 退出后恢复
        assert get_current_trace_id() == "outer_trace"
        assert get_current_span_id() == "outer_span"

        # 清理
        set_current_trace_id("")
        set_current_span_id("")

    def test_context_manager_returns_self(self):
        """__enter__ 应返回 self"""
        ctx = TraceContext()
        with ctx as entered:
            assert entered is ctx

    # ── 嵌套上下文 ────────────────────────────────────

    def test_nested_context(self):
        """嵌套 TraceContext 应正确恢复级联的上层值"""
        outer = TraceContext(trace_id="level_0")
        with outer:
            assert get_current_trace_id() == "level_0"

            inner = TraceContext(trace_id="level_1")
            with inner:
                assert get_current_trace_id() == "level_1"

                deepest = TraceContext(trace_id="level_2")
                with deepest:
                    assert get_current_trace_id() == "level_2"

                # 退出 deepest，恢复 level_1
                assert get_current_trace_id() == "level_1"

            # 退出 inner，恢复 level_0
            assert get_current_trace_id() == "level_0"

    def test_nested_span_id_independent(self):
        """嵌套上下文的 span_id 各自独立"""
        outer = TraceContext()
        inner = TraceContext()

        with outer:
            outer_span = get_current_span_id()
            with inner:
                inner_span = get_current_span_id()
                assert inner_span != outer_span

    # ── set_trace_id ──────────────────────────────────

    def test_set_trace_id(self):
        """set_trace_id 可重新设置 trace_id"""
        ctx = TraceContext(trace_id="original")
        assert ctx.trace_id == "original"

        ctx.set_trace_id("updated")
        assert ctx.trace_id == "updated"

    def test_set_trace_id_affects_context(self):
        """set_trace_id 后进入上下文应反映新值"""
        ctx = TraceContext(trace_id="before")
        ctx.set_trace_id("after")
        with ctx:
            assert get_current_trace_id() == "after"

    # ── get_log_context ──────────────────────────────

    def test_get_log_context_keys(self):
        """get_log_context 返回包含 trace_id 和 span_id 的字典"""
        ctx = TraceContext()
        log_ctx = ctx.get_log_context()
        assert "trace_id" in log_ctx
        assert "span_id" in log_ctx

    def test_get_log_context_values(self):
        """get_log_context 返回正确的值"""
        custom_trace = "trace_custom123456"
        ctx = TraceContext(trace_id=custom_trace)
        log_ctx = ctx.get_log_context()
        assert log_ctx["trace_id"] == custom_trace
        assert log_ctx["span_id"] == ctx.span_id

    def test_get_log_context_after_set_trace_id(self):
        """set_trace_id 后 get_log_context 反映新值"""
        ctx = TraceContext(trace_id="old")
        ctx.set_trace_id("new")
        log_ctx = ctx.get_log_context()
        assert log_ctx["trace_id"] == "new"

    # ── 线程局部存储 ──────────────────────────────────

    def test_thread_trace_id_default_empty(self):
        """默认线程 trace_id 为空字符串"""
        assert get_thread_trace_id() == ""

    def test_set_and_get_thread_trace_id(self):
        """set_thread_trace_id 后 get_thread_trace_id 返回设置的值"""
        test_id = "thread_trace_001"
        set_thread_trace_id(test_id)
        assert get_thread_trace_id() == test_id

    def test_thread_trace_id_isolation(self):
        """不同线程的 thread trace_id 互不干扰"""
        import threading

        main_id = "main_thread"
        set_thread_trace_id(main_id)

        results = {}

        def worker():
            results["before"] = get_thread_trace_id()
            set_thread_trace_id("worker_thread")
            results["after_set"] = get_thread_trace_id()

        t = threading.Thread(target=worker)
        t.start()
        t.join()

        assert results["before"] == ""  # 新线程默认为空
        assert results["after_set"] == "worker_thread"
        # 主线程不受影响
        assert get_thread_trace_id() == main_id

        set_thread_trace_id("")  # 清理

    # ── 整体集成 ──────────────────────────────────────

    def test_trace_propagation_through_context(self):
        """验证 trace_id 通过 contextvars 正确传播"""
        custom_trace = "trace_integration_test"
        ctx = TraceContext(trace_id=custom_trace)

        with ctx:
            assert get_current_trace_id() == custom_trace
            assert get_current_span_id() == ctx.span_id
            # 验证日志上下文同步
            log_ctx = ctx.get_log_context()
            assert log_ctx["trace_id"] == custom_trace
            assert log_ctx["span_id"] == ctx.span_id

    def test_repr(self):
        """__repr__ 返回包含 trace_id 和 span_id 的信息"""
        ctx = TraceContext(trace_id="trace_repr_test")
        rep = repr(ctx)
        assert "trace_repr_test" in rep
        assert ctx.span_id in rep
