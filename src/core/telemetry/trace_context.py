"""追踪上下文 — trace_id 传播与结构化日志上下文

提供 TraceContext 类，用于在 asyncio 任务中传播 trace_id，
支持结构化日志的记录上下文。

使用方式:
    from .trace_context import TraceContext, get_current_trace_id

    # 在请求入口处创建上下文
    ctx = TraceContext()
    ctx.set_trace_id("trace_abc123")

    # 在任意位置获取当前 trace_id
    trace_id = get_current_trace_id()
"""

from __future__ import annotations

import contextvars
import logging
import threading
import uuid
from typing import Optional

_logger = logging.getLogger(__name__)

# ── asyncio 上下文变量（支持 asyncio 任务的自动传播） ──
_trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")
_span_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("span_id", default="")


def generate_trace_id() -> str:
    """生成一个新的 trace_id"""
    return f"trace_{uuid.uuid4().hex[:12]}"


def generate_span_id() -> str:
    """生成一个新的 span_id"""
    return f"span_{uuid.uuid4().hex[:8]}"


def get_current_trace_id() -> str:
    """获取当前 asyncio 上下文的 trace_id"""
    return _trace_id_var.get()


def set_current_trace_id(trace_id: str) -> None:
    """设置当前 asyncio 上下文的 trace_id"""
    _trace_id_var.set(trace_id)


def get_current_span_id() -> str:
    """获取当前 asyncio 上下文的 span_id"""
    return _span_id_var.get()


def set_current_span_id(span_id: str) -> None:
    """设置当前 asyncio 上下文的 span_id"""
    _span_id_var.set(span_id)


class TraceContext:
    """追踪上下文 — 管理 trace_id 和 span_id

    作为上下文管理器使用，在进入/退出时自动设置/恢复 trace_id。
    支持 asyncio 任务的自动上下文传播（通过 contextvars）。

    使用方式:
        ctx = TraceContext()
        with ctx:
            current_id = get_current_trace_id()  # 可获取到 trace_id
    """

    def __init__(self, trace_id: Optional[str] = None):
        self._trace_id = trace_id or generate_trace_id()
        self._span_id = generate_span_id()
        self._previous_trace_id: str = ""
        self._previous_span_id: str = ""

    @property
    def trace_id(self) -> str:
        return self._trace_id

    @property
    def span_id(self) -> str:
        return self._span_id

    def set_trace_id(self, trace_id: str) -> None:
        """设置 trace_id"""
        self._trace_id = trace_id

    def __enter__(self) -> TraceContext:
        self._previous_trace_id = _trace_id_var.get()
        self._previous_span_id = _span_id_var.get()
        _trace_id_var.set(self._trace_id)
        _span_id_var.set(self._span_id)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        _trace_id_var.set(self._previous_trace_id)
        _span_id_var.set(self._previous_span_id)

    # ── 结构化日志绑定 ──────────────────────────────────

    def get_log_context(self) -> dict:
        """获取结构化日志上下文字典"""
        return {
            "trace_id": self._trace_id,
            "span_id": self._span_id,
        }

    def __repr__(self) -> str:
        return f"<TraceContext trace_id={self._trace_id} span_id={self._span_id}>"


# ── 线程局部存储（同步线程的 trace_id 传播） ────────────
_thread_local = threading.local()


def get_thread_trace_id() -> str:
    """获取当前线程的 trace_id（同步线程用）"""
    return getattr(_thread_local, "trace_id", "")


def set_thread_trace_id(trace_id: str) -> None:
    """设置当前线程的 trace_id（同步线程用）"""
    _thread_local.trace_id = trace_id
