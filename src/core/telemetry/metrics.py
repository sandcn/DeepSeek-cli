"""结构化指标收集器 — MetricsCollector

支持三类指标:
- Counter（计数器）: 单调递增，如 token 总量、调用次数
- Gauge（仪表盘）: 可升降，如当前上下文大小、并发数
- Histogram（直方图）: 分布统计，如延迟、工具执行耗时

使用方式:
    from ..core.telemetry import get_default_collector
    metrics = get_default_collector()
    metrics.counter("model.calls", 1)
    metrics.histogram("model.latency_ms", 342.5)
    metrics.gauge("context.chars", 15420)
"""

from __future__ import annotations

import json
import math
import threading
import time
from collections import defaultdict
from typing import Any


# ── 默认百分位配置 ─────────────────────────────────────────
_DEFAULT_PERCENTILES = [50, 90, 95, 99]
# 直方图最大采样数 — 超过后丢弃较早的一半数据
_MAX_HISTOGRAM_SAMPLES = 10000


class MetricsCollector:
    """结构化指标收集器（线程安全）

    使用方式:
        mc = MetricsCollector()
        mc.counter("api.calls", 1)
        mc.histogram("model.latency_ms", 150.0)
        snapshot = mc.snapshot()
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._counters: dict[str, int] = defaultdict(int)
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, list[float]] = defaultdict(list)
        self._start_time = time.time()

    # ── 计数器 ──────────────────────────────────────────

    def counter(self, name: str, value: int = 1) -> None:
        """递增计数器

        Args:
            name: 指标名称（如 "model.calls"）
            value: 增量值，默认 1
        """
        with self._lock:
            self._counters[name] += value

    def get_counter(self, name: str) -> int:
        """读取计数器当前值"""
        with self._lock:
            return self._counters.get(name, 0)

    # ── 仪表盘 ──────────────────────────────────────────

    def gauge(self, name: str, value: float) -> None:
        """设置仪表盘值（可升降）

        Args:
            name: 指标名称（如 "context.chars"）
            value: 当前值
        """
        with self._lock:
            self._gauges[name] = value

    def gauge_add(self, name: str, delta: float) -> None:
        """增减仪表盘值

        Args:
            name: 指标名称
            delta: 变化量（可正可负）
        """
        with self._lock:
            self._gauges[name] = self._gauges.get(name, 0.0) + delta

    def get_gauge(self, name: str) -> float | None:
        """读取仪表盘当前值"""
        with self._lock:
            return self._gauges.get(name)

    # ── 直方图 ──────────────────────────────────────────

    def histogram(self, name: str, value: float) -> None:
        """记录一次观测值到直方图

        Args:
            name: 指标名称（如 "model.latency_ms"）
            value: 观测值
        """
        with self._lock:
            self._histograms[name].append(value)
            # 滑动窗口：超过上限时丢弃最早的一半采样
            samples = self._histograms[name]
            if len(samples) > _MAX_HISTOGRAM_SAMPLES:
                # 保留最近的一半
                half = len(samples) // 2
                self._histograms[name] = samples[-half:]

    def get_histogram_stats(self, name: str,
                            percentiles: list[int] | None = None) -> dict[str, float] | None:
        """计算直方图统计摘要

        Args:
            name: 指标名称
            percentiles: 百分位列表，默认 [50, 90, 95, 99]

        Returns:
            {"count": int, "min": float, "max": float, "avg": float,
             "p50": float, "p90": float, "p95": float, "p99": float}
            无数据时返回 None
        """
        pcts = percentiles or _DEFAULT_PERCENTILES
        with self._lock:
            values = self._histograms.get(name)
            if not values:
                return None
            sorted_vals = sorted(values)

        n = len(sorted_vals)
        result = {
            "count": n,
            "min": sorted_vals[0],
            "max": sorted_vals[-1],
            "avg": sum(sorted_vals) / n,
        }
        for p in pcts:
            idx = max(0, min(n - 1, int(math.ceil(p / 100.0 * n) - 1)))
            result[f"p{p}"] = sorted_vals[idx]
        return result

    def reset_histogram(self, name: str) -> None:
        """重置指定直方图"""
        with self._lock:
            self._histograms[name] = []

    # ── 快照 ────────────────────────────────────────────

    def snapshot(self, reset_histograms: bool = False) -> dict[str, Any]:
        """获取当前全部指标的快照

        Args:
            reset_histograms: 是否在快照后重置直方图数据

        Returns:
            {
                "uptime_seconds": float,
                "counters": dict[str, int],
                "gauges": dict[str, float],
                "histograms": dict[str, dict],  # 每个指标返回统计摘要
            }
        """
        with self._lock:
            counters = dict(self._counters)
            gauges = dict(self._gauges)
            hist_names = list(self._histograms.keys())

        histograms = {}
        for name in hist_names:
            stats = self.get_histogram_stats(name)
            if stats:
                histograms[name] = stats

        if reset_histograms:
            for name in hist_names:
                self.reset_histogram(name)

        return {
            "uptime_seconds": time.time() - self._start_time,
            "counters": counters,
            "gauges": gauges,
            "histograms": histograms,
        }

    def report(self) -> str:
        """格式化输出全部指标，用于终端显示"""
        snap = self.snapshot()
        lines = [f"📊 指标快照 (运行 {snap['uptime_seconds']:.0f}s)"]

        if snap["counters"]:
            lines.append("  ── 计数器 ──")
            for name, val in sorted(snap["counters"].items()):
                lines.append(f"    {name}: {val}")

        if snap["gauges"]:
            lines.append("  ── 仪表盘 ──")
            for name, val in sorted(snap["gauges"].items()):
                lines.append(f"    {name}: {val:.1f}")

        if snap["histograms"]:
            lines.append("  ── 直方图 ──")
            for name, stats in sorted(snap["histograms"].items()):
                lines.append(f"    {name}: count={stats['count']} "
                             f"avg={stats['avg']:.1f} p50={stats['p50']:.1f} "
                             f"p90={stats['p90']:.1f} p99={stats['p99']:.1f}")

        return "\n".join(lines)

    # ── 审计输出（JSON Lines） ───────────────────────────

    def to_jsonl(self) -> str:
        """输出当前指标为 JSON Lines 格式（用于持久化）"""
        snap = self.snapshot()
        record = {
            "ts": time.time(),
            "type": "metrics_snapshot",
            "counters": dict(snap["counters"]),
            "gauges": dict(snap["gauges"]),
            "histograms": {
                name: stats for name, stats in snap["histograms"].items()
            },
        }
        return json.dumps(record, ensure_ascii=False)


# ── 模块级单例 ────────────────────────────────────────────
_default_collector: MetricsCollector | None = None
_collector_lock = threading.RLock()


def get_default_collector() -> MetricsCollector:
    """获取全局默认指标收集器（线程安全单例）"""
    global _default_collector
    if _default_collector is None:
        with _collector_lock:
            if _default_collector is None:
                _default_collector = MetricsCollector()
    return _default_collector


def reset_default_collector() -> None:
    """重置全局默认收集器（主要用于测试）"""
    global _default_collector
    with _collector_lock:
        _default_collector = None
