"""FrameMetrics 与组件契约标记（2026-10-24）。"""

from __future__ import annotations

from types import SimpleNamespace

from src.tui.ink._metrics import FrameMetrics
from src.tui.ink.markers import (
    DATA_INPUT_AREA,
    INPUT_AREA_TYPE,
    STATIC_LINES_TYPE,
    is_input_area_fiber,
    is_static_lines_fiber,
)
def test_metrics_summary_and_format() -> None:
    metrics = FrameMetrics(capacity=3)
    metrics.record(build_ms=1.0, reconcile_ms=2.0, frame_ms=3.0, write_ms=4.0,
                  total_ms=10.0, lines=5, reused=False)
    metrics.record(build_ms=3.0, reconcile_ms=4.0, frame_ms=5.0, write_ms=6.0,
                  total_ms=20.0, lines=7, reused=True)
    summary = metrics.summary()
    assert summary["count"] == 2
    assert summary["reused"] == 1
    assert summary["total_ms"]["max"] == 20.0
    assert "p95" in summary["build_ms"]
    text = FrameMetrics.format_summary(summary)
    assert "total=p50" in text
    metrics.reset()
    assert metrics.summary() == {"count": 0}
    assert FrameMetrics.format_summary(metrics.summary()) == "metrics=empty"


def test_markers_values_are_stable() -> None:
    # 常量值属于跨版本契约（host 类型字符串），变更需同步 registry 与 app 层。
    assert STATIC_LINES_TYPE == "static-lines"
    assert INPUT_AREA_TYPE == "input-area"
    assert DATA_INPUT_AREA == "dataInputArea"


def test_is_input_area_fiber_accepts_legacy_and_marker() -> None:
    legacy = SimpleNamespace(is_host=True, type=INPUT_AREA_TYPE, props={})
    marked = SimpleNamespace(is_host=True, type="box", props={DATA_INPUT_AREA: True})
    other = SimpleNamespace(is_host=True, type="box", props={})
    assert is_input_area_fiber(legacy)
    assert is_input_area_fiber(marked)
    assert not is_input_area_fiber(other)
    assert not is_input_area_fiber(None)


def test_evict_low_cmd_keeps_unfinished_tasks_consistent() -> None:
    """队列腾位必须补 task_done（否则 flush/join 永久等待）。"""
    import queue

    from src.tui.ink._cmd_priority import evict_low_cmd

    cmd_queue: queue.PriorityQueue = queue.PriorityQueue(maxsize=3)
    cmd_queue.put((3, 0, "low"))
    cmd_queue.put((3, 1, "low2"))
    cmd_queue.put((0, 2, "critical"))
    removed = evict_low_cmd(cmd_queue, 3)
    assert removed is not None and removed[0] == 3
    assert cmd_queue.unfinished_tasks == 2
    while True:
        try:
            cmd_queue.get_nowait()
            cmd_queue.task_done()
        except queue.Empty:
            break
    # 记账一致：join 不会永久阻塞。
    cmd_queue.join()


def test_is_static_lines_fiber() -> None:
    fiber = SimpleNamespace(is_host=True, type=STATIC_LINES_TYPE, props={})
    assert is_static_lines_fiber(fiber)
    assert not is_static_lines_fiber(
        SimpleNamespace(is_host=False, type=STATIC_LINES_TYPE, props={})
    )
