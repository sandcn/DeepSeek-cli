"""帧级指标 — 渲染分段耗时与规模的最小可观测实现（2026-10-24）。

背景：框架此前只有"慢帧告警"（单帧总耗时 > 0.5s）与 ``render(debug=True)``
的行数/队列深度输出——无法回答"帧时间花在构建、调和、布局、绘制还是写屏"。
本模块提供**零依赖、固定容量**的环形采样，供：

  - ``InkSession._debug_log_frame`` 输出最近窗口的 P50/P95/max；
  - 测试断言"空闲复用帧不重建"（``reused`` 字段）；
  - 未来接入 CI 性能基线（阈值由测试侧决定，本模块不做判断）。

设计约束：
  - 只在渲染线程写入（``record``），读侧自行承担竞态（快照为普通 dict）；
  - 采样字段固定，避免每帧产生额外分配；容量默认 120（约 2 秒 @60Hz）；
  - 不做统计库依赖：小样本排序即可，避免引入开销与平台差异。
"""

from __future__ import annotations

from collections import deque

__all__ = ["FrameMetrics"]

#: 参与分位数统计的数值字段（顺序即 debug 输出顺序）。
_NUMERIC_FIELDS = (
    "build_ms",
    "reconcile_ms",
    "frame_ms",
    "write_ms",
    "total_ms",
)


class FrameMetrics:
    """固定容量帧指标环形缓冲。"""

    __slots__ = ("_samples", "_last")

    def __init__(self, capacity: int = 120) -> None:
        self._samples: deque = deque(maxlen=max(1, int(capacity)))
        self._last: dict = {}

    def record(self, **fields) -> None:
        """记录一帧（字段缺失按 0 参与统计；仅接受已知字段 + reused/lines）。"""
        sample = {key: fields.get(key, 0) for key in _NUMERIC_FIELDS}
        sample["lines"] = int(fields.get("lines", 0) or 0)
        sample["reused"] = 1 if fields.get("reused") else 0
        self._last = sample
        self._samples.append(sample)

    @property
    def last(self) -> dict:
        """最近一帧的副本。"""
        return dict(self._last)

    def reset(self) -> None:
        """清空采样（会话重启/测试）。"""
        self._samples.clear()
        self._last = {}

    def summary(self) -> dict:
        """最近窗口统计：``{field: {"p50","p95","max"}}`` + 帧数/复用数。"""
        samples = list(self._samples)
        out: dict = {"count": len(samples)}
        if not samples:
            return out
        out["reused"] = sum(sample["reused"] for sample in samples)
        out["lines_max"] = max(sample["lines"] for sample in samples)
        for key in _NUMERIC_FIELDS:
            values = sorted(float(sample[key]) for sample in samples)
            count = len(values)
            out[key] = {
                "p50": values[count // 2],
                "p95": values[min(count - 1, int(count * 0.95))],
                "max": values[-1],
            }
        return out

    @staticmethod
    def format_summary(summary: dict) -> str:
        """把 ``summary()`` 结果格式化为单行（debug 输出用）。"""
        if not summary.get("count"):
            return "metrics=empty"
        parts = [f"n={summary['count']}", f"reused={summary.get('reused', 0)}"]
        for key in ("total_ms", "build_ms", "reconcile_ms", "frame_ms", "write_ms"):
            stat = summary.get(key)
            if stat:
                parts.append(
                    f"{key[:-3]}=p50 {stat['p50']:.2f}/p95 {stat['p95']:.2f}"
                    f"/max {stat['max']:.2f}ms"
                )
        return " ".join(parts)
