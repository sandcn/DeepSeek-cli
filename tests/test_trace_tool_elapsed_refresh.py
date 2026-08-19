"""轨迹 Trace 正运行的工具耗时实时刷新测试（2026-08-19）。

需求：轨迹 Trace 中**正运行的工具耗时没有刷新**——运行中工具（tool box
未关闭 / subagent 运行中工具）耗时随时间增长，但 records 仅在内容变化时
重建（``_records_deps``/``_subagent_trace_deps`` 时间基元素不入指纹）——
rec.time_seconds 为构建时**快照**会冻结：工具无输出/状态不变期间 use_memo
命中，耗时永不刷新。

修复：TraceRecord 增加 ``time_started``/``time_started_monotonic`` 字段
（运行中起始时间戳 + 时间基准：主轨迹工具 box=monotonic、subagent 槽位=
epoch）；渲染层 ``_rec_time_seconds`` 按起始时间戳实时计算耗时（台账行
每帧读取 + 整数秒入指纹、检查器 use_memo deps 整数秒入指纹）→ 每秒刷新
一次，工具无输出期间耗时也持续走动。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.tui.app.trace import TraceRecord


# ═══════════════════════════════════════════════════════════
# 1. _rec_time_seconds 实时耗时计算（渲染层核心）
# ═══════════════════════════════════════════════════════════

class TestRecTimeSeconds:

    def _rec(self, **kw):
        base = dict(
            kind="tool", status="running", time_seconds=0.0,
            time_started=100.0, time_started_monotonic=True,
        )
        base.update(kw)
        return SimpleNamespace(**base)

    def test_running_monotonic_realtime(self, monkeypatch):
        """运行中（monotonic 基准）→ 实时耗时随时间走动（非快照）。"""
        from src.tui.app.trace_view import _rec_time_seconds
        rec = self._rec(time_started=100.0, time_started_monotonic=True)
        monkeypatch.setattr("src.tui.app.trace_view._time.monotonic", lambda: 103.0)
        assert _rec_time_seconds(rec) == pytest.approx(3.0)
        monkeypatch.setattr("src.tui.app.trace_view._time.monotonic", lambda: 107.5)
        assert _rec_time_seconds(rec) == pytest.approx(7.5)

    def test_running_epoch_realtime(self, monkeypatch):
        """运行中（epoch 基准，subagent 槽位）→ 实时耗时随时间走动。"""
        from src.tui.app.trace_view import _rec_time_seconds
        rec = self._rec(time_started=1000.0, time_started_monotonic=False)
        monkeypatch.setattr("src.tui.app.trace_view._time.time", lambda: 1003.0)
        assert _rec_time_seconds(rec) == pytest.approx(3.0)
        monkeypatch.setattr("src.tui.app.trace_view._time.time", lambda: 1008.0)
        assert _rec_time_seconds(rec) == pytest.approx(8.0)

    def test_done_uses_snapshot(self, monkeypatch):
        """已完成（status!=running）→ 返回构建时快照（不随时间漂移）。"""
        from src.tui.app.trace_view import _rec_time_seconds
        rec = self._rec(status="done", time_seconds=12.5, time_started=100.0)
        monkeypatch.setattr("src.tui.app.trace_view._time.monotonic", lambda: 999.0)
        assert _rec_time_seconds(rec) == 12.5

    def test_running_no_started_fallback(self, monkeypatch):
        """运行中但无起始时间戳（异常/旧数据）→ 回退快照。"""
        from src.tui.app.trace_view import _rec_time_seconds
        rec = self._rec(time_started=None, time_seconds=1.0)
        monkeypatch.setattr("src.tui.app.trace_view._time.monotonic", lambda: 999.0)
        assert _rec_time_seconds(rec) == 1.0

    def test_running_invalid_started_fallback(self, monkeypatch):
        """运行中但起始时间戳非数值（异常数据）→ 回退快照不崩溃。"""
        from src.tui.app.trace_view import _rec_time_seconds
        rec = self._rec(time_started="bad", time_seconds=2.0)
        monkeypatch.setattr("src.tui.app.trace_view._time.monotonic", lambda: 999.0)
        assert _rec_time_seconds(rec) == 2.0

    def test_running_future_start_clamped_zero(self, monkeypatch):
        """起始时间戳在未来（时钟倒退）→ 耗时钳制为 0。"""
        from src.tui.app.trace_view import _rec_time_seconds
        rec = self._rec(time_started=200.0, time_started_monotonic=True)
        monkeypatch.setattr("src.tui.app.trace_view._time.monotonic", lambda: 100.0)
        assert _rec_time_seconds(rec) == 0.0


# ═══════════════════════════════════════════════════════════
# 2. 台账行耗时实时刷新（_ledger_row_runs）
# ═══════════════════════════════════════════════════════════

class TestLedgerRowRunsElapsedRefresh:

    def _make_running(self, time_started=100.0):
        return SimpleNamespace(
            index=1, kind="tool", summary="bash pwd", status="running",
            result="", time_seconds=0.0, time_started=time_started,
            time_started_monotonic=True,
        )

    def _text(self, runs):
        return "".join(r.text for r in runs)

    def test_running_elapsed_text_updates_per_second(self, monkeypatch):
        """运行中耗时整数秒变化 → 台账行文本更新（每秒刷新一次）。"""
        from src.tui.app import trace_view
        from src.tui.app.trace_view import _ledger_row_runs
        trace_view._LEDGER_RUNS_CACHE.clear()
        rec = self._make_running(time_started=100.0)
        monkeypatch.setattr("src.tui.app.trace_view._time.monotonic", lambda: 101.0)
        runs1 = _ledger_row_runs(rec, False, 60)
        assert "1.0s" in self._text(runs1)
        # 同整数秒内 → 缓存命中（返回同一 runs 引用，不每帧重建）
        monkeypatch.setattr("src.tui.app.trace_view._time.monotonic", lambda: 101.9)
        runs_same = _ledger_row_runs(rec, False, 60)
        assert runs_same is runs1
        # 跨整数秒 → 重建 + 文本更新（2.0s → 3.5s）
        monkeypatch.setattr("src.tui.app.trace_view._time.monotonic", lambda: 103.5)
        runs2 = _ledger_row_runs(rec, False, 60)
        assert "3.5s" in self._text(runs2)
        assert runs2 is not runs1

    def test_done_elapsed_static(self, monkeypatch):
        """已完成记录耗时静态（不随时间漂移）。"""
        from src.tui.app import trace_view
        from src.tui.app.trace_view import _ledger_row_runs
        trace_view._LEDGER_RUNS_CACHE.clear()
        rec = SimpleNamespace(
            index=1, kind="tool", summary="bash pwd", status="done",
            result="", time_seconds=8.0, time_started=100.0,
            time_started_monotonic=True,
        )
        monkeypatch.setattr("src.tui.app.trace_view._time.monotonic", lambda: 101.0)
        runs1 = _ledger_row_runs(rec, False, 60)
        assert "8.0s" in self._text(runs1)
        monkeypatch.setattr("src.tui.app.trace_view._time.monotonic", lambda: 999.0)
        runs2 = _ledger_row_runs(rec, False, 60)
        assert self._text(runs2) == self._text(runs1)


# ═══════════════════════════════════════════════════════════
# 3. 检查器耗时实时刷新（_inspector_deps / _inspector_children）
# ═══════════════════════════════════════════════════════════

class TestInspectorElapsedRefresh:

    def _rec(self, **kw):
        base = dict(
            kind="tool", index=1, status="running", source_block=None,
            lines=None, tool_args="", tool_result="",
            time_seconds=0.0, time_started=100.0,
            time_started_monotonic=True, tokens={}, subagent_label="",
        )
        base.update(kw)
        return SimpleNamespace(**base)

    def test_inspector_deps_change_per_second(self, monkeypatch):
        """运行中耗时整数秒变化 → 检查器 use_memo deps 变化（触发重建）。"""
        from src.tui.app.trace_view import _inspector_deps
        rec = self._rec()
        monkeypatch.setattr("src.tui.app.trace_view._time.monotonic", lambda: 101.0)
        d1 = _inspector_deps(rec, 40, 24)
        monkeypatch.setattr("src.tui.app.trace_view._time.monotonic", lambda: 102.0)
        d2 = _inspector_deps(rec, 40, 24)
        assert d1 != d2
        # 同整数秒内 deps 稳定（不每帧重建）
        monkeypatch.setattr("src.tui.app.trace_view._time.monotonic", lambda: 102.9)
        d3 = _inspector_deps(rec, 40, 24)
        assert d3 == d2

    def test_inspector_deps_static_when_done(self, monkeypatch):
        """已完成记录 deps 稳定（耗时快照不变）。"""
        from src.tui.app.trace_view import _inspector_deps
        rec = self._rec(status="done", time_seconds=5.0)
        monkeypatch.setattr("src.tui.app.trace_view._time.monotonic", lambda: 101.0)
        d1 = _inspector_deps(rec, 40, 24)
        monkeypatch.setattr("src.tui.app.trace_view._time.monotonic", lambda: 999.0)
        d2 = _inspector_deps(rec, 40, 24)
        assert d1 == d2

    def test_inspector_meta_shows_live_elapsed(self, monkeypatch):
        """检查器 meta 行显示实时耗时（运行中按起始时间戳计算）。"""
        from src.tui.app.trace_view import _inspector_children
        rec = self._rec(time_started=100.0)
        monkeypatch.setattr("src.tui.app.trace_view._time.monotonic", lambda: 103.0)
        children = _inspector_children(rec, 40, 24)
        texts = [str(c.props.get("children", "")) for c in children]
        assert any("耗时 3.0s" in t for t in texts)


# ═══════════════════════════════════════════════════════════
# 4. 构建路径：运行中记录携带 time_started（主轨迹 / 块回退）
# ═══════════════════════════════════════════════════════════

class TestBuildPathsCarryTimeStarted:

    def test_live_records_running_tool_carries_started(self):
        """_live_records（消息源模式）运行中工具记录带起始时间戳（monotonic）。"""
        from src.tui.app.trace import _live_records
        box = SimpleNamespace(closed=False, lines=[], extra={
            "tool_name": "bash", "tool_detail": "pwd",
            "_tool_started_at": 123.0,
        })
        model = SimpleNamespace(
            blocks=[], tool_boxes={"t1": box},
            reasoning_block_index=-1, content_block_index=-1,
        )
        records: list = []
        rows: list = []
        _live_records(model, [0], records, rows)
        assert len(records) == 1
        rec = records[0]
        assert rec.status == "running"
        assert rec.time_started == 123.0
        assert rec.time_started_monotonic is True

    def test_record_from_block_running_tool_carries_started(self):
        """_record_from_block（块回退路径）运行中工具带起始时间戳（monotonic）。"""
        from src.tui.app.trace import _record_from_block
        block = SimpleNamespace(kind="tool", closed=False, lines=[], extra={
            "tool_name": "bash", "tool_detail": "ls",
            "tool_status": "running", "_tool_started_at": 55.0,
        })
        rec = _record_from_block(block, 1)
        assert rec is not None
        assert rec.time_started == 55.0
        assert rec.time_started_monotonic is True

    def test_record_from_block_done_keeps_started_but_static(self, monkeypatch):
        """块回退已完成工具：仍带起始时间戳，但渲染层按快照（不漂移）。"""
        from src.tui.app.trace import _record_from_block
        from src.tui.app.trace_view import _rec_time_seconds
        block = SimpleNamespace(kind="tool", closed=True, lines=[], extra={
            "tool_name": "bash", "tool_detail": "ls",
            "tool_status": "done", "_tool_started_at": 10.0,
            "_tool_duration": 4.0,
        })
        rec = _record_from_block(block, 1)
        assert rec.time_seconds == 4.0
        monkeypatch.setattr("src.tui.app.trace_view._time.monotonic", lambda: 999.0)
        assert _rec_time_seconds(rec) == 4.0  # done → 快照，不随时间漂移


# ═══════════════════════════════════════════════════════════
# 5. 构建路径：subagent 相关记录携带 time_started（epoch）
# ═══════════════════════════════════════════════════════════

class TestSubagentBuildPathsCarryTimeStarted:

    def _tool_record(self, phase="running", start=300.0):
        return SimpleNamespace(
            tool_name="bash", detail="ls", phase=phase,
            start_time=start, end_time=0.0,
        )

    def test_merge_subagent_running_carries_started(self):
        """subagent 合并进 tool 记录（运行中）→ 起始时间戳 epoch。"""
        from src.tui.app.trace import _merge_subagent_into_tool_record
        slot = SimpleNamespace(
            status="running", start_time=200.0, end_time=0.0,
            input_tokens=1, output_tokens=2, live_input_tokens=0,
            live_output_tokens=0, description="desc", model_phase="",
            parse_info="", result_text="", result_error="",
            tool_history=[],
        )
        rec = TraceRecord(index=1, kind="tool", summary="subagent x",
                          lines=["subagent x"])
        _merge_subagent_into_tool_record(rec, slot, "sa-1")
        assert rec.status == "running"
        assert rec.time_started == 200.0
        assert rec.time_started_monotonic is False

    def test_merge_subagent_done_clears_started(self):
        """subagent 已完成合并 → 不携带起始时间戳（快照耗时）。"""
        from src.tui.app.trace import _merge_subagent_into_tool_record
        slot = SimpleNamespace(
            status="done", start_time=100.0, end_time=115.0,
            input_tokens=1, output_tokens=2, live_input_tokens=0,
            live_output_tokens=0, description="desc", model_phase="",
            parse_info="", result_text="ok", result_error="",
            tool_history=[],
        )
        rec = TraceRecord(index=1, kind="tool", summary="subagent x",
                          lines=["subagent x"])
        _merge_subagent_into_tool_record(rec, slot, "sa-1")
        assert rec.status == "done"
        assert rec.time_seconds == 15.0
        assert rec.time_started is None

    def test_subagent_live_records_running_tool_carries_started(self):
        """_subagent_live_records 运行中工具 → 起始时间戳 epoch。"""
        from src.tui.app.trace import _subagent_live_records
        tool = self._tool_record(phase="running", start=300.0)
        slot = SimpleNamespace(
            status="running", model_phase="", tool_history=[tool],
            live_reasoning="", live_content="",
        )
        records: list = []
        rows: list = []
        _subagent_live_records([0], records, rows, slot)
        assert len(records) == 1
        rec = records[0]
        assert rec.status == "running"
        assert rec.time_started == 300.0
        assert rec.time_started_monotonic is False

    def test_subagent_fallback_running_tool_carries_started(self, monkeypatch):
        """_subagent_fallback_records 运行中工具 → 起始时间戳 epoch。"""
        from src.tui.app.trace import _subagent_fallback_records
        monkeypatch.setattr("src.tui.app.trace._tools_record", lambda: None)
        tool = self._tool_record(phase="running", start=400.0)
        slot = SimpleNamespace(
            prompt="hello", tool_history=[tool],
            result_text="", result_error="",
        )
        records, rows = _subagent_fallback_records("sa-1", slot)
        tool_recs = [r for r in records if r.kind == "tool"]
        assert tool_recs
        rec = tool_recs[0]
        assert rec.status == "running"
        assert rec.time_started == 400.0
        assert rec.time_started_monotonic is False

    def test_subagent_fallback_done_no_started(self, monkeypatch):
        """_subagent_fallback_records 已完成工具 → 快照耗时无起始时间戳。"""
        from src.tui.app.trace import _subagent_fallback_records
        monkeypatch.setattr("src.tui.app.trace._tools_record", lambda: None)
        tool = SimpleNamespace(
            tool_name="bash", detail="ls", phase="done",
            start_time=400.0, end_time=410.0,
        )
        slot = SimpleNamespace(
            prompt="hello", tool_history=[tool],
            result_text="", result_error="",
        )
        records, rows = _subagent_fallback_records("sa-1", slot)
        tool_recs = [r for r in records if r.kind == "tool"]
        rec = tool_recs[0]
        assert rec.status == "done"
        assert rec.time_seconds == 10.0
        assert rec.time_started is None


# ═══════════════════════════════════════════════════════════
# 6. TraceRecord 默认值（向后兼容）
# ═══════════════════════════════════════════════════════════

class TestTraceRecordDefaults:

    def test_defaults_backward_compatible(self):
        """新增字段默认值：time_started=None、monotonic=True（旧代码零回归）。"""
        rec = TraceRecord(index=1, kind="user", summary="hi")
        assert rec.time_started is None
        assert rec.time_started_monotonic is True
        assert rec.time_seconds is None

    def test_explicit_monotonic_epoch(self):
        """显式构造两种时间基准字段（构建路径设置）。"""
        rec = TraceRecord(time_started=1.0, time_started_monotonic=False)
        assert rec.time_started == 1.0
        assert rec.time_started_monotonic is False
