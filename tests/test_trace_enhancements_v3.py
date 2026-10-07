"""轨迹 Trace 第三批增强测试（2026-10-07 用户需求：更好的显示 / 操作 / 更多功能）。

覆盖：
  1. 台账耗时条形图（``_time_bar_fill`` / ``_time_bar_max`` + 渲染）；
  2. 轮次折叠（``_collapse_turns`` 纯函数；``za``/``zc``/``zo``/``zC``/``zO``）；
  3. 记录对比（``compare_panel_rows`` + ``C`` 键 + 右栏对比面板）；
  4. 导出范围（``_cycle_export_scope`` / ``_export_records_for_scope`` + ``x``）。

风格对齐既有 ``test_trace_*.py``：真实 Reconciler 渲染 + use_input handler
直调（纯函数另测）。
"""

from __future__ import annotations

from types import SimpleNamespace

from src.tui.app.model import AppModel
from src.tui.app.trace import TraceRecord
from src.tui.ink import h
from src.tui.ink.fiber import InputHook
from src.tui.ink.reconciler import Reconciler

# ═══════════════════════════════════════════════════════════
# 通用辅助
# ═══════════════════════════════════════════════════════════


def _render_root(component, props, width=100, height=24):
    rec = Reconciler(schedule_callback=None)
    root = rec.create_root()
    rec.render(root, h(component, props), width, height)
    return rec, root


def _find_input_handler(fiber):
    if fiber is None:
        return None
    for hook in getattr(fiber, "hooks", None) or []:
        if isinstance(hook, InputHook) and hook.is_active and hook.handler is not None:
            return hook.handler
    r = _find_input_handler(fiber.child)
    if r is not None:
        return r
    return _find_input_handler(fiber.sibling)


def _ev(kind: str, char: str = ""):
    return SimpleNamespace(
        kind=kind, char=char, modifier=0, keycode=0, raw=b"",
        kitty_bits=-1, event_type="",
    )


def _rec(index, summary, **kw):
    base = dict(
        index=index, kind="user", summary=summary, status="", time_seconds=None,
        time_started=None, time_started_monotonic=True, tokens={}, result="",
        lines=[], source_block=None, subagent_label="", tool_call_id="",
        tool_name="", tool_args=None, tool_result="", images=[],
    )
    base.update(kw)
    return TraceRecord(**base)


def _frame_text(frame) -> str:
    return "\n".join("".join(r.text for r in line.runs) for line in frame.lines)


def _render_frame(root, width):
    from src.tui.ink import components as _components
    from src.tui.ink.layout import layout_tree
    layout_tree(root, width)
    return _components.render_frame(root, width)


def _setup(monkeypatch, records, rows=None, width=120, **model_attrs):
    from src.tui.app.trace_view import TraceView
    rows = rows if rows is not None else records
    monkeypatch.setattr(
        "src.tui.app.trace_view.build_trace_records", lambda m: (records, rows),
    )
    model = AppModel()
    model.fullscreen = "trace"
    for k, v in model_attrs.items():
        setattr(model, k, v)
    _rec_obj, root = _render_root(TraceView, {"model": model, "width": width})
    return model, _find_input_handler(root), root


# ═══════════════════════════════════════════════════════════
# 1. 台账耗时条形图
# ═══════════════════════════════════════════════════════════


class TestTimeBar:

    def test_time_bar_fill_normalizes(self):
        from src.tui.app.trace_ledger import _time_bar_fill
        assert _time_bar_fill(5.0, 10.0, 10) == (5, 10)
        assert _time_bar_fill(10.0, 10.0, 10) == (10, 10)
        # 有耗时但占比极小 → 至少 1 格
        assert _time_bar_fill(0.01, 100.0, 10) == (1, 10)

    def test_time_bar_fill_empty(self):
        from src.tui.app.trace_ledger import _time_bar_fill
        assert _time_bar_fill(None, 10.0, 6) == (0, 6)
        assert _time_bar_fill(0, 10.0, 6) == (0, 6)
        assert _time_bar_fill(5, 0, 6) == (0, 6)
        assert _time_bar_fill(5, 10, 0) == (0, 0)

    def test_time_bar_max(self):
        from src.tui.app.trace_ledger import _time_bar_max
        recs = [_rec(1, "a", time_seconds=1.0), _rec(2, "b", time_seconds=3.5),
                None, _rec(3, "c")]
        assert _time_bar_max(recs) == 3.5
        assert _time_bar_max([]) == 0.0

    def test_time_bar_rendered(self, monkeypatch):
        records = [
            _rec(1, "fast", kind="tool", time_seconds=1.0),
            _rec(2, "slow", kind="tool", time_seconds=10.0),
        ]
        model, handler, root = _setup(
            monkeypatch, records, width=120, trace_show_time_bar=True,
        )
        text = _frame_text(_render_frame(root, 120))
        assert "\u2588" in text  # 填充格
        assert "\u2591" in text  # 背景格

    def test_time_bar_off(self, monkeypatch):
        records = [_rec(1, "slow", kind="tool", time_seconds=10.0)]
        _model, _handler, root = _setup(
            monkeypatch, records, width=120, trace_show_time_bar=False,
        )
        text = _frame_text(_render_frame(root, 120))
        assert "\u2591" not in text


# ═══════════════════════════════════════════════════════════
# 2. 轮次折叠
# ═══════════════════════════════════════════════════════════


class TestTurnCollapse:

    def _rows(self):
        return [
            _rec(1, "system"),
            None, _rec(2, "u1"), _rec(3, "a1"),
            None, _rec(4, "u2"), _rec(5, "a2"),
        ]

    def test_collapse_turns_pure(self):
        from src.tui.app.trace_view import _collapse_turns, _TraceTurnCollapsedRow
        rows = self._rows()
        out = _collapse_turns(rows, {1})
        assert out[0] is rows[0]
        assert isinstance(out[1], _TraceTurnCollapsedRow)
        assert out[1].turn == 1 and out[1].count == 2
        # 轮次 2 保留
        assert any(isinstance(r, TraceRecord) and r.index == 4 for r in out)

    def test_collapse_turns_empty_noop(self):
        from src.tui.app.trace_view import _collapse_turns
        rows = self._rows()
        assert _collapse_turns(rows, set()) is rows
        assert _collapse_turns(rows, None) is rows

    def test_collapse_turns_bad_input(self):
        from src.tui.app.trace_view import _collapse_turns
        rows = self._rows()
        assert _collapse_turns(rows, {"x"}) is rows  # 非法元素 → 空集合

    def test_za_toggles_current_turn(self, monkeypatch):
        rows = self._rows()
        records = [r for r in rows if isinstance(r, TraceRecord)]
        model, handler, _root = _setup(monkeypatch, records, rows)
        # 选中 u1（records 索引 1，轮次 1）
        model.trace_selected = 1
        assert handler(_ev("char", "z")) is True
        assert model.trace_pending_prefix == "z"
        assert handler(_ev("char", "a")) is True
        assert model.trace_collapsed_turns == {1}
        # 再按 za 展开
        assert handler(_ev("char", "z")) is True
        assert handler(_ev("char", "a")) is True
        assert model.trace_collapsed_turns == set()

    def test_zc_zo(self, monkeypatch):
        rows = self._rows()
        records = [r for r in rows if isinstance(r, TraceRecord)]
        model, handler, _root = _setup(monkeypatch, records, rows)
        model.trace_selected = 1
        assert handler(_ev("char", "z")) is True
        assert handler(_ev("char", "c")) is True
        assert model.trace_collapsed_turns == {1}
        assert handler(_ev("char", "z")) is True
        assert handler(_ev("char", "o")) is True
        assert model.trace_collapsed_turns == set()

    def test_zC_zO_all(self, monkeypatch):
        rows = self._rows()
        records = [r for r in rows if isinstance(r, TraceRecord)]
        model, handler, _root = _setup(monkeypatch, records, rows)
        assert handler(_ev("char", "z")) is True
        assert handler(_ev("char", "C")) is True
        assert model.trace_collapsed_turns == {1, 2}
        assert handler(_ev("char", "z")) is True
        assert handler(_ev("char", "O")) is True
        assert model.trace_collapsed_turns == set()

    def test_collapse_renders_header(self, monkeypatch):
        rows = self._rows()
        records = [r for r in rows if isinstance(r, TraceRecord)]
        model, _handler, root = _setup(
            monkeypatch, records, rows, trace_collapsed_turns={1},
        )
        text = _frame_text(_render_frame(root, 120))
        assert "\u8f6e\u6b21 1" in text  # 轮次 1
        assert "\u6298\u53e0 2 \u6761" in text
        assert "u1" not in text  # 折叠记录隐藏

    def test_collapse_moves_selection_out(self, monkeypatch):
        rows = self._rows()
        records = [r for r in rows if isinstance(r, TraceRecord)]
        model, handler, _root = _setup(monkeypatch, records, rows)
        model.trace_selected = 1  # u1（轮次 1）
        assert handler(_ev("char", "z")) is True
        assert handler(_ev("char", "c")) is True
        # 选中记录被折叠 → 移到之前可见记录（system #1）
        assert model.trace_selected == 0

    def test_jump_turn(self, monkeypatch):
        rows = self._rows()
        records = [r for r in rows if isinstance(r, TraceRecord)]
        model, handler, _root = _setup(monkeypatch, records, rows)
        model.trace_selected = 0  # system（轮次 0）
        assert handler(_ev("char", "}")) is True
        assert model.trace_selected == 1  # u1（轮次 1 首条）
        assert handler(_ev("char", "}")) is True
        assert model.trace_selected == 3  # u2（轮次 2 首条）
        assert handler(_ev("char", "{")) is True
        assert model.trace_selected == 1


# ═══════════════════════════════════════════════════════════
# 3. 记录对比
# ═══════════════════════════════════════════════════════════


class TestTraceCompare:

    def test_compare_panel_rows(self):
        from src.tui.app.trace_compare import compare_panel_rows
        a = _rec(1, "aa", kind="tool", tool_name="Bash",
                 tool_args='{"cmd":"ls"}', tool_result="out-a")
        b = _rec(2, "bb", kind="tool", tool_name="Read",
                 tool_args='{"file":"x"}', tool_result="out-b")
        rows = compare_panel_rows(a, b, 60)
        text = "\n".join("".join(r.text for r in row) for row in rows)
        assert "\u8bb0\u5f55\u5bf9\u6bd4" in text
        assert "Bash" in text and "Read" in text
        for row in rows:
            assert sum(getattr(r, "width", 1) for r in row) <= 60

    def test_compare_panel_empty(self):
        from src.tui.app.trace_compare import compare_panel_rows
        rows = compare_panel_rows(None, None, 40)
        assert rows
        text = "\n".join("".join(r.text for r in row) for row in rows)
        assert "(\u65e0\u8bb0\u5f55)" in text

    def test_compare_fields(self):
        from src.tui.app.trace_compare import _compare_fields
        fields = dict(_compare_fields(_rec(
            1, "a", kind="tool", tool_name="Bash", status="done",
            tool_args="{}", tool_call_id="call_1",
        )))
        assert fields["工具"] == "Bash"
        assert fields["状态"] == "done"
        assert fields["调用ID"] == "call_1"

    def test_C_key_selects_two(self, monkeypatch):
        records = [_rec(1, "a", kind="tool"), _rec(2, "b", kind="tool")]
        model, handler, _root = _setup(monkeypatch, records)
        model.trace_selected = 0
        assert handler(_ev("char", "C")) is True
        assert model.trace_compare == [1]
        model.trace_selected = 1
        assert handler(_ev("char", "C")) is True
        assert model.trace_compare == [1, 2]

    def test_C_key_third_resets(self, monkeypatch):
        records = [_rec(1, "a"), _rec(2, "b"), _rec(3, "c")]
        model, handler, _root = _setup(monkeypatch, records)
        model.trace_selected = 0
        handler(_ev("char", "C"))
        model.trace_selected = 1
        handler(_ev("char", "C"))
        model.trace_selected = 2
        handler(_ev("char", "C"))
        assert model.trace_compare == [3]

    def test_compare_panel_rendered(self, monkeypatch):
        records = [
            _rec(1, "alpha", kind="tool", tool_name="Bash", tool_args="{}",
                 tool_result="r1"),
            _rec(2, "beta", kind="tool", tool_name="Grep", tool_args="{}",
                 tool_result="r2"),
        ]
        model, _handler, root = _setup(monkeypatch, records, trace_compare=[1, 2])
        text = _frame_text(_render_frame(root, 120))
        assert "\u8bb0\u5f55\u5bf9\u6bd4" in text
        assert "Bash" in text and "Grep" in text


# ═══════════════════════════════════════════════════════════
# 4. 导出范围
# ═══════════════════════════════════════════════════════════


class TestExportScope:

    def test_cycle_export_scope(self):
        from src.tui.app.trace_view import _cycle_export_scope
        model = AppModel()
        assert model.trace_export_scope == "all"
        assert _cycle_export_scope(model) == "view"
        assert _cycle_export_scope(model) == "errors"
        assert _cycle_export_scope(model) == "tools"
        assert _cycle_export_scope(model) == "all"

    def test_export_records_for_scope(self):
        from src.tui.app.trace_view import _export_records_for_scope
        recs = [
            _rec(1, "u"), _rec(2, "t", kind="tool"),
            _rec(3, "f", kind="tool", status="fail"),
        ]
        assert _export_records_for_scope("all", recs, recs) == recs
        assert [r.index for r in _export_records_for_scope("errors", recs, recs)] == [3]
        assert [r.index for r in _export_records_for_scope("tools", recs, recs)] == [2, 3]
        assert [r.index for r in _export_records_for_scope("view", recs, [recs[0]])] == [1]

    def test_x_key_cycles(self, monkeypatch):
        records = [_rec(1, "a"), _rec(2, "b")]
        model, handler, _root = _setup(monkeypatch, records)
        assert handler(_ev("char", "x")) is True
        assert model.trace_export_scope == "view"
        assert handler(_ev("char", "x")) is True
        assert model.trace_export_scope == "errors"

    def test_export_scope_in_status(self, monkeypatch):
        records = [_rec(1, "a")]
        model, _handler, root = _setup(
            monkeypatch, records, trace_export_scope="tools",
        )
        text = _frame_text(_render_frame(root, 120))
        assert "\u5bfc\u51fa \u4ec5\u5de5\u5177" in text

    def test_do_export_empty_scope(self, monkeypatch, tmp_path):
        from src.tui.app.trace_view import _do_export
        model = AppModel()
        _do_export(model, [], "md", "")
        assert "\u7a7a" in model.trace_status_message

    def test_do_export_writes(self, monkeypatch, tmp_path):
        from src.tui.app import trace_view as tv
        from src.tui.app.trace_view import _do_export
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(tv, "write_export", lambda records, fmt, source: "out.md")
        model = AppModel()
        _do_export(model, [_rec(1, "a")], "md", "", "全部")
        assert "out.md" in model.trace_status_message
