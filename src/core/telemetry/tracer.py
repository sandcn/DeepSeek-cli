"""调用链追踪 — Tracer + Span

轻量级调用链追踪，用于记录和分析模型调用、工具执行的耗时分布。
基于 Span 树结构，支持父-子 Span 嵌套。

使用方式:
    from ..core.telemetry import get_default_tracer
    tracer = get_default_tracer()
    with tracer.span("model.call") as span:
        span.set_attribute("model", "deepseek-v4")
        # ... 执行模型调用 ...
    # span 自动结束
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from typing import Any, Generator

_logger = logging.getLogger(__name__)

# 用于 Span ID 生成的简单计数器
_id_counter = 0
_id_lock = threading.RLock()


def _next_id() -> str:
    """生成单调递增的 Span ID"""
    global _id_counter
    with _id_lock:
        _id_counter += 1
        return f"span_{_id_counter:06d}"


class Span:
    """调用链跨度 — 记录一次操作的开始/结束/属性

    通常不直接实例化，通过 Tracer.start_span() 或 tracer.span() 上下文管理器创建。
    """

    def __init__(self, name: str, span_id: str | None = None,
                 parent_span_id: str | None = None):
        self.name = name
        self.span_id = span_id or _next_id()
        self.parent_span_id = parent_span_id
        self.start_time = time.monotonic()
        self.end_time: float | None = None
        self.attrs: dict[str, Any] = {}
        self.status: str = "ok"  # ok | error
        self.error_message: str = ""

    def set_attribute(self, key: str, value: Any) -> None:
        """设置 Span 属性"""
        self.attrs[key] = value

    def set_status(self, status: str, message: str = "") -> None:
        """设置状态

        Args:
            status: "ok" 或 "error"
            message: 错误消息（status="error" 时使用）
        """
        self.status = status
        if message:
            self.error_message = message

    def finish(self) -> None:
        """结束 Span（记录结束时间）"""
        if self.end_time is None:
            self.end_time = time.monotonic()

    @property
    def duration_ms(self) -> float:
        """Span 持续时间（毫秒）"""
        end = self.end_time or time.monotonic()
        return (end - self.start_time) * 1000

    @property
    def is_finished(self) -> bool:
        return self.end_time is not None

    def to_dict(self) -> dict:
        """序列化为字典"""
        return {
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "name": self.name,
            "duration_ms": round(self.duration_ms, 1),
            "status": self.status,
            "error": self.error_message or None,
            "attrs": {k: str(v) for k, v in self.attrs.items()},
        }

    def __repr__(self) -> str:
        return (f"<Span {self.name} id={self.span_id} "
                f"dur={self.duration_ms:.1f}ms status={self.status}>")


class Tracer:
    """调用链追踪器

    维护一个 Span 栈，支持嵌套追踪。
    全局默认实例通过 get_default_tracer() 获取。

    使用方式:
        tracer = Tracer()
        with tracer.span("model_call") as span:
            span.set_attribute("model", "deepseek-v4")
            with tracer.span("http_request") as sub:
                ...
    """

    def __init__(self, max_spans: int = 1000):
        self._max_spans = max_spans
        self._stack: list[Span] = []
        self._finished_spans: list[Span] = []
        self._lock = threading.RLock()

    # ── 创建 Span ───────────────────────────────────────

    def start_span(self, name: str) -> Span:
        """开始一个新的 Span（自动关联父 Span）"""
        parent_id = None
        with self._lock:
            if self._stack:
                parent_id = self._stack[-1].span_id
            span = Span(name, parent_span_id=parent_id)
            self._stack.append(span)
        return span

    def end_span(self) -> Span | None:
        """结束栈顶 Span，返回该 Span"""
        with self._lock:
            if not self._stack:
                _logger.warning("end_span 调用时栈为空")
                return None
            span = self._stack.pop()
            span.finish()

            if len(self._finished_spans) < self._max_spans:
                self._finished_spans.append(span)

        return span

    @contextmanager
    def span(self, name: str) -> Generator[Span, None, None]:
        """上下文管理器：自动 start/end Span

        用法:
            with tracer.span("model_call") as span:
                span.set_attribute("model", "deepseek-v4")
                result = call_model()
        """
        s = self.start_span(name)
        try:
            yield s
        except Exception as e:
            s.set_status("error", str(e))
            self.end_span()
            raise
        else:
            self.end_span()

    # ── 当前 Span ───────────────────────────────────────

    @property
    def current_span(self) -> Span | None:
        """返回栈顶 Span（当前活跃的 Span）"""
        with self._lock:
            return self._stack[-1] if self._stack else None

    @property
    def active_count(self) -> int:
        """当前活跃 Span 数"""
        with self._lock:
            return len(self._stack)

    # ── 快照 ────────────────────────────────────────────

    def snapshot(self) -> list[dict]:
        """获取所有已完成 Span 的快照（按结束时间排序）"""
        with self._lock:
            return [s.to_dict() for s in self._finished_spans[-100:]]

    def tree(self) -> list[dict]:
        """构建 Span 树结构（按父-子关系组织）"""
        with self._lock:
            spans = list(self._finished_spans)

        # 按 span_id 建立映射
        span_map = {s.span_id: s.to_dict() for s in spans}
        roots = []
        for s in spans:
            d = span_map[s.span_id]
            if s.parent_span_id and s.parent_span_id in span_map:
                parent = span_map[s.parent_span_id]
                parent.setdefault("children", []).append(d)
            else:
                roots.append(d)
        return roots

    def report(self) -> str:
        """格式化输出追踪报告"""
        tree = self.tree()
        lines = ["🔗 调用链追踪"]

        def _print_tree(nodes, indent=0):
            prefix = "  " * indent
            for node in nodes:
                dur = node.get("duration_ms", 0)
                status = node.get("status", "ok")
                mark = "✅" if status == "ok" else "❌"
                lines.append(f"{prefix}{mark} {node['name']} ({dur:.0f}ms)")
                if node.get("error"):
                    lines.append(f"{prefix}  error: {node['error']}")
                for k, v in node.get("attrs", {}).items():
                    lines.append(f"{prefix}  {k}={v}")
                if "children" in node:
                    _print_tree(node["children"], indent + 1)

        _print_tree(tree)
        return "\n".join(lines)

    def clear(self) -> None:
        """清理所有已完成 Span"""
        with self._lock:
            self._finished_spans.clear()
            self._stack.clear()


# ── 模块级单例 ────────────────────────────────────────────
_default_tracer: Tracer | None = None
_tracer_lock = threading.RLock()


def get_default_tracer() -> Tracer:
    """获取全局默认追踪器（线程安全单例）"""
    global _default_tracer
    if _default_tracer is None:
        with _tracer_lock:
            if _default_tracer is None:
                _default_tracer = Tracer()
    return _default_tracer


def reset_default_tracer() -> None:
    """重置全局默认追踪器（主要用于测试）"""
    global _default_tracer
    with _tracer_lock:
        _default_tracer = None
