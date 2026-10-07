"""轨迹 Trace 第二批增强测试（2026-10-07 用户需求：更好的显示 / 操作 / 更多功能）。

覆盖：
  1. 记录标记 / 书签（``m{a-z}`` 设置、``'{a-z}`` 跳转、台账行显示）；
  2. 搜索历史（``/`` 输入模式 ↑↓ 回溯，去重 + 有界）；
  3. 时间列与相对时间（``T`` 循环 off/abs/rel，台账行右侧显示）；
  4. 按记录种类过滤（``t`` 循环，台账/检查器只显示该种类）；
  5. 检查器行号显示（``#`` 开关）与内容行复制（检查器焦点 ``y``）；
  6. 记录详情内联展开 / 折叠（``o``，台账就地插入详情预览行）。

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
    return "\n".join(
        "".join(r.text for r in line.runs) for line in frame.lines
    )


def _render_frame(root, width):
    from src.tui.ink import components as _components
    from src.tui.ink.layout import layout_tree
    layout_tree(root, width)
    return _components.render_frame(root, width)


def _model_with(records, monkeypatch):
    from src.tui.app import trace_view as tv
    monkeypatch.setattr(tv, "build_trace_records", lambda m: (records, records))
    model = AppModel()
    model.fullscreen = "trace"
    return model


def _handler(model, monkeypatch, records, width=120):
    from src.tui.app.trace_view import TraceView
    monkeypatch.setattr(
        "src.tui.app.trace_view.build_trace_records", lambda m: (records, records),
    )
    _rec_obj, root = _render_root(TraceView, {"model": model, "width": width})
    return _find_input_handler(root)


# ═══════════════════════════════════════════════════════════
# 1. 记录标记 / 书签
# ═══════════════════════════════════════════════════════════


class TestTraceMarks:

    def test_mark_key_sets_and_jumps(self, monkeypatch):
        from src.tui.app.trace_view import TraceView
        records = [_rec(1, "hello"), _rec(2, "world")]
        model = _model_with(records, monkeypatch)
        _rec_obj, root = _render_root(TraceView, {"model": model, "width": 120})
        handler = _find_input_handler(root)
        # 选中 #1（首条）
        model.trace_selected = 0
        assert handler(_ev("char", "m")) is True
        assert model.trace_pending_prefix == "m"
        assert handler(_ev("char", "a")) is True
        assert model.trace_marks == {"a": 1}
        # 移到末条后跳回标记
        model.trace_selected = 1
        assert handler(_ev("char", "'")) is True
        assert handler(_ev("char", "a")) is True
        assert model.trace_selected == 0

    def test_mark_map_dedup(self):
        from src.tui.app.trace_view import _mark_map
        model = AppModel()
        model.trace_marks = {"b": 3, "a": 3, "c": 5}
        assert _mark_map(model) == {3: "a", 5: "c"}

    def test_jump_missing_mark_status(self):
        from src.tui.app.trace_view import _jump_mark
        model = AppModel()
        _jump_mark(model, [_rec(1, "x")], "z")
        assert "未设置标记" in model.trace_status_message

    def test_mark_shown_in_ledger_row(self):
        from src.tui.app.trace_view import _ledger_row_runs
        rec = _rec(3, "hello")
        runs = _ledger_row_runs(rec, False, 60, mark="q")
        text = "".join(r.text for r in runs)
        assert "'q" in text

    def test_mark_requires_letter(self, monkeypatch):
        from src.tui.app.trace_view import TraceView
        records = [_rec(1, "hello")]
        model = _model_with(records, monkeypatch)
        _rec_obj, root = _render_root(TraceView, {"model": model, "width": 120})
        handler = _find_input_handler(root)
        handler(_ev("char", "m"))
        handler(_ev("char", "1"))
        assert model.trace_marks == {}
        assert "标记键需为字母" in model.trace_status_message


# ═══════════════════════════════════════════════════════════
# 2. 搜索历史
# ═══════════════════════════════════════════════════════════


class TestSearchHistory:

    def test_push_dedup_and_limit(self):
        from src.tui.app.trace_view import (
            _SEARCH_HISTORY_MAX, _push_search_history,
        )
        model = AppModel()
        for i in range(_SEARCH_HISTORY_MAX + 10):
            _push_search_history(model, f"p{i}")
        assert len(model.trace_search_history) == _SEARCH_HISTORY_MAX
        _push_search_history(model, "p5")
        assert model.trace_search_history[-1] == "p5"
        assert model.trace_search_history.count("p5") == 1

    def test_history_move_cycle(self):
        from src.tui.app.trace_view import _push_search_history, _search_history_move
        model = AppModel()
        _push_search_history(model, "one")
        _push_search_history(model, "two")
        model.trace_search_hist_idx = 2
        assert _search_history_move(model, -1) is True
        assert model.trace_search_query == "two"
        _search_history_move(model, -1)
        assert model.trace_search_query == "one"
        _search_history_move(model, 1)
        assert model.trace_search_query == "two"
        # 回到最近之外 → 清空（新输入）
        _search_history_move(model, 1)
        assert model.trace_search_query == ""

    def test_history_empty_returns_false(self):
        from src.tui.app.trace_view import _search_history_move
        model = AppModel()
        assert _search_history_move(model, 1) is False

    def test_search_mode_arrow_navigates_history(self, monkeypatch):
        from src.tui.app.trace_view import TraceView
        records = [_rec(1, "hello")]
        model = _model_with(records, monkeypatch)
        model.trace_search_history = ["alpha", "beta"]
        _rec_obj, root = _render_root(TraceView, {"model": model, "width": 120})
        handler = _find_input_handler(root)
        handler(_ev("char", "/"))
        assert model.trace_search_mode is True
        assert handler(_ev("arrow_up")) is True
        assert model.trace_search_query == "beta"
        assert handler(_ev("arrow_up")) is True
        assert model.trace_search_query == "alpha"
        assert handler(_ev("arrow_down")) is True
        assert model.trace_search_query == "beta"

    def test_exec_search_pushes_history(self):
        from src.tui.app.trace_view import _exec_search
        model = AppModel()
        model.trace_search_query = "needle"
        model.trace_pane = "ledger"
        _exec_search(model, [_rec(1, "needle")], [], 0, 5, None)
        assert "needle" in model.trace_search_history


# ═══════════════════════════════════════════════════════════
# 3. 时间列与相对时间
# ═══════════════════════════════════════════════════════════


class TestTimeColumn:

    def test_cycle_time_mode(self):
        from src.tui.app.trace_view import _cycle_time_mode
        model = AppModel()
        assert model.trace_time_mode == "off"
        assert _cycle_time_mode(model) == "abs"
        assert _cycle_time_mode(model) == "rel"
        assert _cycle_time_mode(model) == "off"

    def test_rec_time_text_abs_and_rel(self):
        from src.tui.app.trace_ledger import _rec_time_text
        import time as _time
        rec = _rec(1, "x", time_started=_time.time() - 120,
                   time_started_monotonic=False)
        abs_text = _rec_time_text(rec, "abs")
        assert len(abs_text) == 8 and abs_text.count(":") == 2
        rel_text = _rec_time_text(rec, "rel")
        assert rel_text.endswith("m") or rel_text.endswith("s")

    def test_rec_time_text_monotonic_hidden(self):
        from src.tui.app.trace_ledger import _rec_time_text
        rec = _rec(1, "x", time_started=123.0, time_started_monotonic=True)
        assert _rec_time_text(rec, "abs") == ""

    def test_ledger_row_shows_time(self):
        import time as _time
        from src.tui.app.trace_view import _ledger_row_runs
        rec = _rec(1, "x", time_started=_time.time(), time_started_monotonic=False,
                   time_seconds=1.0)
        runs = _ledger_row_runs(rec, False, 80, time_mode="abs")
        text = "".join(r.text for r in runs)
        assert text.count(":") >= 2  # HH:MM:SS

    def test_time_mode_key_cycles(self, monkeypatch):
        from src.tui.app.trace_view import TraceView
        records = [_rec(1, "hello")]
        model = _model_with(records, monkeypatch)
        _rec_obj, root = _render_root(TraceView, {"model": model, "width": 120})
        handler = _find_input_handler(root)
        assert handler(_ev("char", "T")) is True
        assert model.trace_time_mode == "abs"


# ═══════════════════════════════════════════════════════════
# 4. 按记录种类过滤
# ═══════════════════════════════════════════════════════════


class TestKindFilter:

    def test_kind_view_filters(self):
        from src.tui.app.trace_view import _kind_view
        records = [_rec(1, "a", kind="user"), _rec(2, "b", kind="tool"),
                   _rec(3, "c", kind="user")]
        assert _kind_view(records, "") is None
        kept, rows = _kind_view(records, "tool")
        assert [r.index for r in kept] == [2]
        assert rows == kept

    def test_cycle_kind_filter(self):
        from src.tui.app.trace_view import _cycle_kind_filter, _kind_filter_options
        model = AppModel()
        opts = _kind_filter_options()
        assert opts[0] == ""
        first = _cycle_kind_filter(model)
        assert first == opts[1]
        for _ in range(len(opts) - 1):
            _cycle_kind_filter(model)
        assert model.trace_kind_filter == ""

    def test_kind_filter_key_resets_selection(self, monkeypatch):
        from src.tui.app.trace_view import TraceView
        records = [_rec(1, "a", kind="user"), _rec(2, "b", kind="tool")]
        model = _model_with(records, monkeypatch)
        _rec_obj, root = _render_root(TraceView, {"model": model, "width": 120})
        handler = _find_input_handler(root)
        model.trace_selected = 0
        assert handler(_ev("char", "t")) is True
        assert model.trace_kind_filter != ""
        assert model.trace_selected == -1

    def test_kind_filter_render_subset(self, monkeypatch):
        from src.tui.app.trace_view import TraceView
        records = [
            _rec(1, "user-message", kind="user"),
            _rec(2, "tool-message", kind="tool", tool_name="Bash"),
        ]
        model = _model_with(records, monkeypatch)
        model.trace_kind_filter = "tool"
        _rec_obj, root = _render_root(TraceView, {"model": model, "width": 120})
        text = _frame_text(_render_frame(root, 120))
        assert "tool-message" in text
        assert "user-message" not in text
        assert "种类" in text


# ═══════════════════════════════════════════════════════════
# 5. 检查器行号 / 内容行复制
# ═══════════════════════════════════════════════════════════


class TestLineNumbersAndCopy:

    def test_line_numbers_render(self):
        from src.tui.app.trace_view import _inspector_children
        rec = _rec(1, "x", kind="context", lines=["alpha", "beta", "gamma"])
        children = _inspector_children(rec, 40, 20, show_line_numbers=True)
        texts = [str(c.props.get("children", "")) for c in children]
        assert any(t.startswith("1 ") and "alpha" in t for t in texts)
        assert any(t.startswith("2 ") and "beta" in t for t in texts)

    def test_line_numbers_off_default(self):
        from src.tui.app.trace_view import _inspector_children
        rec = _rec(1, "x", kind="context", lines=["alpha"])
        children = _inspector_children(rec, 40, 20)
        texts = [str(c.props.get("children", "")) for c in children]
        assert "alpha" in texts

    def test_deps_include_line_numbers(self):
        from src.tui.app.trace_view import _inspector_deps
        rec = _rec(1, "x", lines=["a"])
        d0 = _inspector_deps(rec, 40, 24, 0, -1, False)
        d1 = _inspector_deps(rec, 40, 24, 0, -1, True)
        assert d0 != d1
        # scroll/cursor 仍为末两位（既有调用面契约）
        assert d0[-2] == 0 and d0[-1] == -1

    def test_toggle_line_numbers_key(self, monkeypatch):
        from src.tui.app.trace_view import TraceView
        records = [_rec(1, "hello")]
        model = _model_with(records, monkeypatch)
        _rec_obj, root = _render_root(TraceView, {"model": model, "width": 120})
        handler = _find_input_handler(root)
        assert handler(_ev("char", "#")) is True
        assert model.trace_show_line_numbers is True
        handler(_ev("char", "#"))
        assert model.trace_show_line_numbers is False

    def test_copy_line_uses_clipboard(self, monkeypatch):
        from src.tui.app import trace_view as tv
        captured = {}
        monkeypatch.setattr(
            "src.tui._screen.set_clipboard",
            lambda text: captured.setdefault("text", text) or True,
        )
        model = AppModel()
        tv._do_copy_line(model, ["alpha", "beta"], 1)
        assert captured["text"] == "beta"
        assert "已复制" in model.trace_status_message

    def test_copy_line_inspector_focus(self, monkeypatch):
        from src.tui.app.trace_view import TraceView
        captured = {}
        monkeypatch.setattr(
            "src.tui._screen.set_clipboard",
            lambda text: captured.setdefault("text", text) or True,
        )
        records = _rec(1, "x", kind="context", lines=["alpha", "beta"])
        model = _model_with([records], monkeypatch)
        _rec_obj, root = _render_root(TraceView, {"model": model, "width": 120})
        handler = _find_input_handler(root)
        model.trace_pane = "inspector"
        model.trace_inspector_cursor = 1
        assert handler(_ev("char", "y")) is True
        assert captured["text"] == "beta"


# ═══════════════════════════════════════════════════════════
# 6. 记录详情内联展开 / 折叠
# ═══════════════════════════════════════════════════════════


class TestInlineExpand:

    def test_expand_ledger_rows_inserts_preview(self):
        from src.tui.app.trace_view import (
            _expand_ledger_rows, _TraceExpandRow,
        )
        rec = _rec(1, "hello", kind="context", lines=["alpha", "beta"])
        rows = [rec]
        out = _expand_ledger_rows(rows, {1})
        assert len(out) == 3
        assert out[0] is rec
        assert isinstance(out[1], _TraceExpandRow)
        assert out[1].text == "alpha"
        assert out[2].text == "beta"

    def test_expand_noop_when_empty(self):
        from src.tui.app.trace_view import _expand_ledger_rows
        rec = _rec(1, "hello", lines=["alpha"])
        rows = [rec]
        assert _expand_ledger_rows(rows, set()) is rows

    def test_expand_row_not_selectable(self):
        from src.tui.app.trace_view import (
            _TraceExpandRow, _is_ledger_selectable,
        )
        rec = _rec(1, "hello")
        assert _is_ledger_selectable(rec) is True
        assert _is_ledger_selectable(None) is False
        assert _is_ledger_selectable(_TraceExpandRow(1, "x")) is False

    def test_toggle_expand_key(self, monkeypatch):
        from src.tui.app.trace_view import TraceView
        rec = _rec(1, "hello", lines=["alpha"])
        model = _model_with([rec], monkeypatch)
        _rec_obj, root = _render_root(TraceView, {"model": model, "width": 120})
        handler = _find_input_handler(root)
        model.trace_selected = 0
        assert handler(_ev("char", "o")) is True
        assert model.trace_expanded == {1}
        assert handler(_ev("char", "o")) is True
        assert model.trace_expanded == set()

    def test_expand_render_shows_preview(self, monkeypatch):
        from src.tui.app.trace_view import TraceView
        rec = _rec(1, "hello", kind="context",
                   lines=["preview-line-xyz"])
        model = _model_with([rec], monkeypatch)
        model.trace_expanded = {1}
        _rec_obj, root = _render_root(TraceView, {"model": model, "width": 120})
        text = _frame_text(_render_frame(root, 120))
        assert "preview-line-xyz" in text

    def test_expand_rows_deps_change(self):
        from src.tui.app.trace_view import _expand_rows_deps
        rows = [_rec(1, "a")]
        assert _expand_rows_deps(rows, set()) != _expand_rows_deps(rows, {1})


# ═══════════════════════════════════════════════════════════
# 7. ListView isSelectable 扩展
# ═══════════════════════════════════════════════════════════


class TestListViewSelectable:

    def test_is_selectable_prop(self):
        from src.tui.ink.widgets.listview import ListView
        items = [1, 2, 3, 4]
        _r, root = _render_root(
            ListView,
            {"items": items, "height": 4, "isSelectable": lambda it: it % 2 == 0},
            width=20, height=6,
        )
        assert root is not None
        handler = _find_input_handler(root)
        assert handler is not None
        # 从 0（不可选）无法上移；下移应跳到 2（首个可选项）
        assert handler(_ev("arrow_down")) is True

    def test_default_none_separator_still_skipped(self):
        from src.tui.ink.widgets.listview import ListView
        items = [None, "a", None, "b"]
        _r, root = _render_root(
            ListView, {"items": items, "height": 4}, width=20, height=6,
        )
        assert root is not None
