"""帧/输入可观测性指标回归测试。

覆盖（架构改进第一批）：
  1. ``InkSession.metrics()`` 记录帧数、帧耗时（last/avg/max）、帧行数；
  2. INPUT 阶段记录事件数与处理耗时；
  3. 队列深度可读；
  4. 渲染线程去掉尾段 1ms 忙等自旋后，60Hz 节拍仍由 ``render_interval`` 保证
     （帧数不超过 60Hz 上限，且空闲会话仍持续渲染）。
"""

from __future__ import annotations

import io
import time

import pytest

from src.tui._config import TuiConfig
from src.tui._screen import TerminalWidthCache
from src.tui.ink import TEXT, h
from src.tui.ink._render_api import _SimpleModel
from src.tui.ink.session import InkSession


class _FakeInput:
    """记录 process_events 返回值的最小 Input 桩。"""

    def __init__(self, events: int = 0) -> None:
        self.events = events
        self.calls = 0

    def process_events(self) -> int:
        self.calls += 1
        return self.events


def _make_session(model=None) -> InkSession:
    def _build_tree(model, width):
        return h(TEXT, {"children": "metrics"})

    return InkSession(
        model=model if model is not None else _SimpleModel(),
        build_tree=_build_tree,
        stream=io.StringIO(),
        width_cache=TerminalWidthCache(),
        config=TuiConfig.defaults(),
    )


def test_metrics_records_frame_and_input():
    session = _make_session()
    session._input = _FakeInput(events=3)
    session._phase_process_input()
    session._render_frame()

    metrics = session.metrics()
    assert metrics["frames"] == 1
    assert metrics["last_frame_lines"] >= 1
    assert metrics["last_frame_ms"] >= 0.0
    assert metrics["avg_frame_ms"] == pytest.approx(metrics["last_frame_ms"])
    assert metrics["max_frame_ms"] >= metrics["last_frame_ms"]
    assert metrics["input_events"] == 3
    assert metrics["last_input_ms"] >= 0.0
    assert metrics["queue_depth"] == 0
    assert metrics["build_ms"] >= 0.0
    assert metrics["reconcile_ms"] >= 0.0
    assert session._input.calls == 1


def test_metrics_accumulate_across_frames():
    session = _make_session()
    session._render_frame()
    session._render_frame()

    metrics = session.metrics()
    assert metrics["frames"] == 2
    assert metrics["avg_frame_ms"] >= 0.0
    assert metrics["max_frame_ms"] >= metrics["avg_frame_ms"]


def test_input_polling_continues_while_idle_frames_skipped():
    """变更驱动：空闲跳过重建，但输入轮询仍按 60Hz 节拍执行。"""
    session = _make_session()
    session._input = _FakeInput(events=0)
    session.set_idle_frame_predicate(lambda: True)
    started = time.monotonic()
    session.start()
    try:
        time.sleep(0.5)
    finally:
        session.stop()
    elapsed = time.monotonic() - started
    expected_ticks = elapsed / TuiConfig.defaults().render_interval
    metrics = session.metrics()
    # 空闲时不再逐帧重建（仅首帧/少量更新）
    assert metrics["frames"] <= 5, f"空闲帧仍在持续重建：{metrics['frames']}"
    # 输入轮询每节拍一次
    assert session._input.calls >= expected_ticks * 0.6, (
        f"输入轮询被跳过：calls={session._input.calls} expected≈{expected_ticks:.1f}"
    )


class _RevisionModel(_SimpleModel):
    """带 revision/touch 的最小模型（验证状态版本驱动重建）。"""

    def __init__(self) -> None:
        super().__init__()
        self._rev = 0

    def touch(self) -> None:
        self._rev += 1

    @property
    def revision(self) -> int:
        return self._rev


def test_queue_metrics_max_depth_and_dropped():
    from src.tui._const import ContentCmd

    session = _make_session()
    for i in range(3):
        session.push_cmd(ContentCmd(text=str(i)))
    session._drain_commands_locked()

    metrics = session.metrics()
    assert metrics["queue_max_depth"] >= 3
    assert metrics["queue_depth"] == 0
    assert metrics["queue_dropped"] == 0


def test_model_revision_triggers_rebuild():
    model = _RevisionModel()
    session = _make_session(model=model)
    session.set_idle_frame_predicate(lambda: True)
    session._render_frame()
    assert session._last_model_revision == 0
    assert session._should_render() is False      # 无变更：跳过

    model.touch()
    assert session._should_render() is True       # revision 变化：重建
    session._render_frame()
    assert session._should_render() is False      # 已消费：继续跳过
