"""追踪上下文测试 — 覆盖 src/core/telemetry/trace_context.py。

验证 trace_id/span_id 生成与上下文管理器传播。
"""

import pytest

from src.core.telemetry.trace_context import (
    TraceContext,
    generate_span_id,
    generate_trace_id,
    get_current_trace_id,
)


def test_generate_trace_id_prefix():
    tid = generate_trace_id()
    assert tid.startswith("trace_")


def test_generate_span_id_prefix():
    sid = generate_span_id()
    assert sid.startswith("span_")


def test_trace_context_default_trace_id():
    ctx = TraceContext()
    assert ctx.trace_id.startswith("trace_")
    assert ctx.span_id.startswith("span_")


def test_trace_context_custom_trace_id():
    ctx = TraceContext(trace_id="trace_my_id")
    assert ctx.trace_id == "trace_my_id"


def test_trace_context_set_trace_id():
    ctx = TraceContext()
    ctx.set_trace_id("trace_new")
    assert ctx.trace_id == "trace_new"


def test_trace_context_enter_exit_propagates():
    ctx = TraceContext(trace_id="trace_test")
    with ctx:
        assert get_current_trace_id() == "trace_test"


def test_trace_context_exit_restores():
    ctx = TraceContext(trace_id="trace_inner")
    with ctx:
        pass
    # 退出后恢复为之前的值（默认为空字符串）
    assert get_current_trace_id() == ""


def test_trace_context_log_context():
    ctx = TraceContext(trace_id="trace_x")
    log_ctx = ctx.get_log_context()
    assert log_ctx["trace_id"] == "trace_x"
    assert "span_id" in log_ctx
