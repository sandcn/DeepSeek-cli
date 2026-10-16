"""轨迹 Trace 增强测试（2026-10-07 用户需求：显示信息 / 操作 / 更多功能）。

覆盖（用户全选的九项增强）：
  1. 头部统计条（条数/轮次/工具/失败/耗时/token/位置 n/total）；
  2. 检查器元信息增强（工具名、调用 ID、状态、起始时间、token 明细、
     参数与返回行数、内容行数）；
  3. 台账行增强（轮次标记 tN、失败高亮、子代理标记 ↳）；
  4. 键位操作增强（e/E 上下失败、]/[ 上下工具、数字+G 跳记录号、
     zR/zM 全展开/全折叠、Ctrl+D/U 半屏翻页）；
  5. 搜索增强（底部匹配计数、过滤模式、大小写敏感开关）；
  6. 视图内帮助面板（? 开关 + 滚动）；
  7. 导出轨迹（w/W 导出 Markdown/JSON）；
  8. 统计概览（i 面板：token 汇总/工具耗时排行/成功率）；
  9. 复制记录内容（y → OSC52 剪贴板）。

风格对齐既有 ``test_trace_*.py``：真实 Reconciler 渲染 + use_input handler
直调（纯函数另测）。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.tui.app.trace import TraceRecord
from src.tui.app.model import AppModel
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
    """查找 fiber 树中第一个活跃 use_input handler。"""
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


def _tool_rec(index, name, **kw):
    kw.setdefault("kind", "tool")
    kw.setdefault("tool_name", name)
    kw.setdefault("summary", name)
    return _rec(index, kw.pop("summary"), **kw)


def _render_frame(root, width):
    from src.tui.ink import components as _components
    from src.tui.ink.layout import layout_tree
    layout_tree(root, width)
    return _components.render_frame(root, width)


def _frame_text(frame) -> str:
    return "\n".join(
        "".join(r.text for r in line.runs) for line in frame.lines
    )


def _setup_view(monkeypatch, records, **model_kw):
    """渲染 TraceView（记录固定为 ``records``）并返回 (model, handler)。"""
    from src.tui.app import trace_view as tv
    from src.tui.app.trace_view import TraceView
    monkeypatch.setattr(tv, "build_trace_records", lambda model: (records, records))
    model = AppModel()
    model.fullscreen = "trace"
    for key, value in model_kw.items():
        setattr(model, key, value)
    _rec_obj, root = _render_root(TraceView, {"model": model, "width": 100})
    return model, _find_input_handler(root)


# ═══════════════════════════════════════════════════════════
# 1. 统计纯函数（collect_trace_stats / format_summary / stats_panel_rows）
# ═══════════════════════════════════════════════════════════

class TestTraceStats:

    def test_collect_empty(self):
        from src.tui.app.trace_stats import collect_trace_stats
        stats = collect_trace_stats([])
        assert stats["total"] == 0
        assert stats["turns"] == 0
        assert stats["tools"] == 0
        assert stats["success_rate"] is None

    def test_collect_counts_and_tokens(self):
        from src.tui.app.trace_stats import collect_trace_stats
        records = [
            _rec(0, "工具列表", kind="tools"),
            _rec(1, "你好", kind="user"),
            _rec(2, "思考", kind="reasoning"),
            _tool_rec(3, "bash", status="done", time_seconds=2.0,
                      tokens={"input": 100, "output": 50}),
            _tool_rec(4, "bash", status="error", time_seconds=1.0),
            _tool_rec(5, "read_file", status="running", time_seconds=0.5),
        ]
        stats = collect_trace_stats(records)
        assert stats["total"] == 6
        assert stats["turns"] == 1
        assert stats["tools"] == 3
        assert stats["tools_done"] == 1
        assert stats["tools_failed"] == 1
        assert stats["tools_running"] == 1
        assert stats["errors"] == 1
        assert stats["running"] == 1
        assert stats["tokens_in"] == 100
        assert stats["tokens_out"] == 50
        assert abs(stats["elapsed"] - 3.0) < 1e-6
        assert stats["success_rate"] == pytest.approx(0.5)
        # 工具排行：bash 耗时 3.0s 居首
        assert stats["tool_ranking"][0][0] == "bash"
        assert stats["tool_ranking"][0][1] == 2

    def test_format_summary_parts(self):
        from src.tui.app.trace_stats import collect_trace_stats, format_summary
        records = [
            _rec(1, "hi", kind="user"),
            _tool_rec(2, "bash", status="done", time_seconds=1.5),
        ]
        stats = collect_trace_stats(records)
        text = format_summary(stats, 2, 2)
        assert "2 条" in text
        assert "1 轮" in text
        assert "工具 1/1" in text
        assert "2/2" in text

    def test_stats_panel_rows_sections(self):
        from src.tui.app.trace_stats import stats_panel_rows
        records = [
            _rec(1, "hi", kind="user"),
            _tool_rec(2, "bash", status="done", time_seconds=1.5),
            _tool_rec(3, "bash", status="error", time_seconds=0.5),
        ]
        rows = stats_panel_rows(records, 40)
        text = "\n".join("".join(r.text for r in row) for row in rows)
        assert "概览" in text
        assert "工具耗时排行" in text
        assert "记录种类分布" in text
        assert "成功率" in text
        # 行宽不变量
        for row in rows:
            assert sum(getattr(r, "width", 0) for r in row) <= 40

    def test_stats_panel_rows_empty(self):
        from src.tui.app.trace_stats import stats_panel_rows
        rows = stats_panel_rows([], 30)
        text = "\n".join("".join(r.text for r in row) for row in rows)
        assert "概览" in text


# ═══════════════════════════════════════════════════════════
# 2. 帮助面板（trace_help）
# ═══════════════════════════════════════════════════════════

class TestTraceHelp:

    def test_help_rows_content(self):
        from src.tui.app.trace_help import help_panel_rows
        rows = help_panel_rows(44)
        text = "\n".join("".join(r.text for r in row) for row in rows)
        assert "导航" in text
        assert "搜索" in text
        assert "Ctrl+D" in text
        assert "zR" in text
        for row in rows:
            assert sum(getattr(r, "width", 0) for r in row) <= 44

    def test_help_toggle_open_close(self, monkeypatch):
        model, handler = _setup_view(monkeypatch, [_rec(1, "hi")])
        assert handler(_ev("char", "?")) is True
        assert model.trace_help_open is True
        # 打开期间吞掉普通字符（不导航/不搜索）
        assert handler(_ev("char", "/")) is True
        assert model.trace_search_mode is False
        # 再按 ? 关闭
        assert handler(_ev("char", "?")) is True
        assert model.trace_help_open is False

    def test_help_escape_closes_only_help(self, monkeypatch):
        model, handler = _setup_view(monkeypatch, [_rec(1, "hi")])
        handler(_ev("char", "?"))
        assert handler(_ev("escape")) is True
        assert model.trace_help_open is False
        assert model.trace_open is True  # 视图仍打开


# ═══════════════════════════════════════════════════════════
# 3. 导出（trace_export）
# ═══════════════════════════════════════════════════════════

class TestTraceExport:

    def test_records_to_markdown(self):
        from src.tui.app.trace_export import records_to_markdown
        records = [
            _rec(1, "你好", kind="user"),
            _tool_rec(2, "bash", status="done", tool_args='{"cmd": "ls"}',
                      tool_result="out", time_seconds=1.0),
        ]
        text = records_to_markdown(records, "主轨迹")
        assert "# 轨迹导出" in text
        assert "## #2 tool" in text
        assert '"cmd": "ls"' in text

    def test_records_to_json_roundtrip(self):
        from src.tui.app.trace_export import records_to_json
        records = [_rec(1, "你好", kind="user")]
        payload = json.loads(records_to_json(records))
        assert payload["meta"]["records"] == 1
        assert payload["records"][0]["summary"] == "你好"

    def test_export_filename(self):
        from src.tui.app.trace_export import export_filename
        name = export_filename("json", when=0)
        assert name.startswith("trace-export-")
        assert name.endswith(".json")

    def test_write_export(self, tmp_path):
        from src.tui.app.trace_export import write_export
        records = [_rec(1, "hi")]
        path = write_export(records, "md", "主轨迹", directory=str(tmp_path))
        with open(tmp_path / path.split("/")[-1].split("\\")[-1], encoding="utf-8") as fh:
            assert "轨迹导出" in fh.read()

    def test_record_to_text(self):
        from src.tui.app.trace_export import record_to_text
        rec = _tool_rec(3, "bash", status="done", tool_call_id="call_1",
                        tool_args='{"a": 1}', tool_result="ok", lines=["bash ls"])
        text = record_to_text(rec)
        assert "#3 tool [done]" in text
        assert "工具 bash" in text
        assert "调用 ID call_1" in text
        assert '"a": 1' in text


# ═══════════════════════════════════════════════════════════
# 4. 键位操作增强
# ═══════════════════════════════════════════════════════════

class TestTraceKeyOps:

    def _mixed(self):
        return [
            _rec(1, "hi", kind="user"),
            _tool_rec(2, "bash", status="done"),
            _rec(3, "出错了", status="error"),
            _tool_rec(4, "read_file", status="running"),
            _rec(5, "再错一次", status="fail"),
        ]

    def test_next_prev_error(self, monkeypatch):
        model, handler = _setup_view(monkeypatch, self._mixed(),
                                     trace_selected=0)
        assert handler(_ev("char", "e")) is True
        assert model.trace_selected == 2  # 首个 error
        assert handler(_ev("char", "e")) is True
        assert model.trace_selected == 4  # 下一个 fail
        assert handler(_ev("char", "e")) is True
        assert model.trace_selected == 4  # 无更多
        assert "无更多失败记录" in model.trace_status_message
        assert handler(_ev("char", "E")) is True
        assert model.trace_selected == 2

    def test_next_prev_tool(self, monkeypatch):
        model, handler = _setup_view(monkeypatch, self._mixed(),
                                     trace_selected=0)
        assert handler(_ev("char", "]")) is True
        assert model.trace_selected == 1
        assert handler(_ev("char", "]")) is True
        assert model.trace_selected == 3
        assert handler(_ev("char", "[")) is True
        assert model.trace_selected == 1

    def test_count_goto_record(self, monkeypatch):
        model, handler = _setup_view(monkeypatch, self._mixed(),
                                     trace_selected=0)
        assert handler(_ev("char", "4")) is True
        assert model.trace_count_buffer == "4"
        assert handler(_ev("char", "G")) is True
        assert model.trace_selected == 3  # 记录 #4
        assert model.trace_count_buffer == ""

    def test_count_goto_missing(self, monkeypatch):
        model, handler = _setup_view(monkeypatch, self._mixed(),
                                     trace_selected=0)
        handler(_ev("char", "9"))
        handler(_ev("char", "G"))
        assert "无记录 #9" in model.trace_status_message

    def test_zr_zm_collapse(self, monkeypatch):
        from src.tui.app import trace_view as tv
        rec = _tool_rec(2, "bash", tool_args='{"a": {"b": 1}}',
                        tool_result="x")
        model, handler = _setup_view(monkeypatch, [_rec(1, "hi"), rec],
                                     trace_selected=1)
        # 渲染一帧以生成 row_keys（可折叠节点 key）
        monkeypatch.setattr(tv, "build_trace_records",
                            lambda m: ([_rec(1, "hi"), rec], [_rec(1, "hi"), rec]))
        from src.tui.app.trace_view import TraceView
        _rec_obj, root = _render_root(TraceView, {"model": model, "width": 100})
        handler = _find_input_handler(root)
        assert handler(_ev("char", "z")) is True
        assert model.trace_pending_prefix == "z"
        assert handler(_ev("char", "M")) is True
        assert model.trace_tree_collapsed  # 有折叠节点
        assert handler(_ev("char", "z")) is True
        assert handler(_ev("char", "R")) is True
        assert model.trace_tree_collapsed == set()

    def test_ctrl_half_page_inspector(self, monkeypatch):
        long_rec = _rec(1, "长内容", kind="content",
                        lines=[f"line-{i}" for i in range(60)])
        model, handler = _setup_view(
            monkeypatch, [long_rec], trace_pane="inspector",
            trace_selected=0,
        )
        model.trace_inspector_cursor = 0
        assert handler(_ev("ctrl_key", "\x04")) is True
        assert model.trace_inspector_cursor > 0
        prev = model.trace_inspector_cursor
        assert handler(_ev("ctrl_key", "\x15")) is True
        assert model.trace_inspector_cursor < prev

    def test_ctrl_half_page_ledger(self, monkeypatch):
        model, handler = _setup_view(monkeypatch, self._mixed(),
                                     trace_selected=0)
        assert handler(_ev("ctrl_key", "\x04")) is True
        assert model.trace_selected > 0


# ═══════════════════════════════════════════════════════════
# 5. 过滤 / 搜索增强
# ═══════════════════════════════════════════════════════════

class TestTraceFilterAndSearch:

    def _records(self):
        return [
            _rec(1, "hello world"),
            _rec(2, "foo bar"),
            _rec(3, "Hello again"),
        ]

    def test_filter_view_mapping(self):
        from src.tui.app.trace_view import _filter_view
        records = self._records()
        view, rows, mapping = _filter_view(records, [0, 2])
        assert [r.index for r in view] == [1, 3]
        assert rows == view
        assert mapping == {0: 0, 2: 1}

    def test_toggle_filter_requires_search(self, monkeypatch):
        model, handler = _setup_view(monkeypatch, self._records())
        assert handler(_ev("char", "f")) is True
        assert model.trace_search_filter is False
        assert "过滤" in model.trace_status_message

    def test_toggle_filter_after_search(self, monkeypatch):
        model, handler = _setup_view(monkeypatch, self._records())
        handler(_ev("char", "/"))
        for ch in "hello":
            handler(_ev("char", ch))
        handler(_ev("enter"))
        assert model.trace_search_matches == [0, 2]
        assert handler(_ev("char", "f")) is True
        assert model.trace_search_filter is True
        assert model.trace_selected == 0
        # 关闭过滤 → 尾部跟随
        assert handler(_ev("char", "f")) is True
        assert model.trace_search_filter is False
        assert model.trace_selected == -1

    def test_search_case_toggle(self, monkeypatch):
        model, handler = _setup_view(monkeypatch, self._records())
        handler(_ev("char", "/"))
        for ch in "hello":
            handler(_ev("char", ch))
        handler(_ev("enter"))
        assert model.trace_search_matches == [0, 2]  # 默认忽略大小写
        assert handler(_ev("char", "v")) is True
        assert model.trace_search_case is True
        assert model.trace_search_matches == [0]  # 区分大小写

    def test_status_line_match_count(self, monkeypatch):
        model, handler = _setup_view(monkeypatch, self._records())
        handler(_ev("char", "/"))
        for ch in "hello":
            handler(_ev("char", ch))
        handler(_ev("enter"))
        from src.tui.app.trace_view import _status_line_text
        text = _status_line_text(model)
        assert "/hello" in text
        assert "1/2" in text


# ═══════════════════════════════════════════════════════════
# 6. 复制 / 导出按键
# ═══════════════════════════════════════════════════════════

class TestTraceCopyExportKeys:

    def test_copy_calls_clipboard(self, monkeypatch):
        import src.tui._screen as screen
        calls = {}
        monkeypatch.setattr(screen, "set_clipboard",
                            lambda text: calls.setdefault("text", text) or True)
        model, handler = _setup_view(monkeypatch, [_rec(1, "hi", kind="user")],
                                     trace_selected=0)
        assert handler(_ev("char", "y")) is True
        assert "已复制" in model.trace_status_message
        assert "hi" in calls["text"]

    def test_export_key(self, monkeypatch):
        from src.tui.app import trace_view as tv
        calls = {}

        def fake_export(records, fmt, source, **kw):
            calls["fmt"] = fmt
            return "trace-export-x.md"

        monkeypatch.setattr(tv, "write_export", fake_export)
        model, handler = _setup_view(monkeypatch, [_rec(1, "hi")])
        assert handler(_ev("char", "w")) is True
        assert calls["fmt"] == "md"
        assert "已导出" in model.trace_status_message
        assert handler(_ev("char", "W")) is True
        assert calls["fmt"] == "json"


# ═══════════════════════════════════════════════════════════
# 7. 台账行 / 检查器 meta 增强
# ═══════════════════════════════════════════════════════════

class TestLedgerAndMetaEnhancements:

    def test_ledger_row_turn_marker(self):
        from src.tui.app.trace_view import _ledger_row_runs
        rec = _rec(3, "hi")
        runs = _ledger_row_runs(rec, False, 60, turn=2)
        text = "".join(r.text for r in runs)
        assert "t2" in text

    def test_ledger_row_error_highlight(self):
        from src.tui.app.trace_view import _ledger_row_runs
        rec = _tool_rec(3, "bash", status="error")
        runs = _ledger_row_runs(rec, False, 60)
        summary_run = next(r for r in runs if r.text == "bash")
        assert summary_run.style is not None and summary_run.style.fg == 196

    def test_ledger_row_subagent_marker(self):
        from src.tui.app.trace_view import _ledger_row_runs
        rec = _tool_rec(3, "subagent", subagent_label="sa-1")
        runs = _ledger_row_runs(rec, False, 60)
        assert any("\u21b3" in r.text for r in runs)

    def test_row_turn_map(self):
        from src.tui.app.trace_ledger import _row_turn_map
        r1, r2 = _rec(1, "a"), _rec(2, "b")
        rows = [None, r1, r2]
        mapping = _row_turn_map(rows)
        assert mapping[1] == 1
        assert mapping[2] == 1

    def test_meta_parts_tool(self):
        from src.tui.app.trace_view import _meta_parts
        rec = _tool_rec(3, "bash", status="done", tool_call_id="call_abcdefghij",
                        tool_args='{"a": 1}', tool_result="line1\nline2",
                        time_seconds=1.2, tokens={"input": 10, "output": 5})
        text = " \u00b7 ".join(_meta_parts(rec, 7))
        assert "工具 bash" in text
        assert "call_abcdefghij" in text
        assert "状态 done" in text
        assert "参数 1 行" in text
        assert "返回 2 行" in text
        assert "内容 7 行" in text
        assert "输入 10" in text

    def test_meta_fixed_rows_consistent(self):
        from src.tui.app.trace_view import _inspector_fixed_rows, _meta_parts
        rec = _tool_rec(3, "bash", status="done")
        assert _inspector_fixed_rows(rec) == 3  # 标题 2 + meta 1
        plain = _rec(9, "hi")
        assert _inspector_fixed_rows(plain) == 2  # 无 meta


# ═══════════════════════════════════════════════════════════
# 8. model 字段默认值与清屏复位
# ═══════════════════════════════════════════════════════════

class TestModelEnhancementFields:

    def test_defaults(self):
        model = AppModel()
        assert model.trace_count_buffer == ""
        assert model.trace_pending_prefix == ""
        assert model.trace_help_open is False
        assert model.trace_stats_open is False
        assert model.trace_status_message == ""
        assert model.trace_search_case is False
        assert model.trace_search_filter is False

    def test_reset_display(self):
        model = AppModel()
        model.trace_count_buffer = "12"
        model.trace_pending_prefix = "z"
        model.trace_help_open = True
        model.trace_stats_open = True
        model.trace_status_message = "msg"
        model.trace_search_case = True
        model.trace_search_filter = True
        model.reset_display()
        assert model.trace_count_buffer == ""
        assert model.trace_pending_prefix == ""
        assert model.trace_help_open is False
        assert model.trace_stats_open is False
        assert model.trace_status_message == ""
        assert model.trace_search_case is False
        assert model.trace_search_filter is False


# ═══════════════════════════════════════════════════════════
# 9. TraceView 渲染：头部统计条 / 统计面板 / 状态行
# ═══════════════════════════════════════════════════════════

class TestTraceViewRendering:

    def _records(self):
        return [
            _rec(1, "hi", kind="user"),
            _tool_rec(2, "bash", status="done", time_seconds=1.5),
        ]

    def test_header_stats(self, monkeypatch):
        from src.tui.app import trace_view as tv
        from src.tui.app.trace_view import TraceView
        records = self._records()
        monkeypatch.setattr(tv, "build_trace_records",
                            lambda m: (records, records))
        model = AppModel()
        model.fullscreen = "trace"
        _rec_obj, root = _render_root(TraceView, {"model": model, "width": 120})
        text = _frame_text(_render_frame(root, 120))
        head = text.split("\n")[0]
        assert "轨迹 Trace" in head
        assert "2 条" in head
        assert "工具 1/1" in head

    def test_stats_panel_toggle_and_render(self, monkeypatch):
        from src.tui.app import trace_view as tv
        from src.tui.app.trace_view import TraceView
        records = self._records()
        monkeypatch.setattr(tv, "build_trace_records",
                            lambda m: (records, records))
        model = AppModel()
        model.fullscreen = "trace"
        _rec_obj, root = _render_root(TraceView, {"model": model, "width": 120})
        handler = _find_input_handler(root)
        assert handler(_ev("char", "i")) is True
        assert model.trace_stats_open is True
        _rec_obj, root = _render_root(TraceView, {"model": model, "width": 120})
        text = _frame_text(_render_frame(root, 120))
        assert "工具耗时排行" in text
        assert "统计面板：i 关闭" in text

    def test_status_line_rendered(self, monkeypatch):
        from src.tui.app import trace_view as tv
        from src.tui.app.trace_view import TraceView
        records = self._records()
        monkeypatch.setattr(tv, "build_trace_records",
                            lambda m: (records, records))
        model = AppModel()
        model.fullscreen = "trace"
        model.trace_status_message = "自定义提示"
        _rec_obj, root = _render_root(TraceView, {"model": model, "width": 120})
        text = _frame_text(_render_frame(root, 120))
        assert "自定义提示" in text

    def test_help_panel_render(self, monkeypatch):
        from src.tui.app import trace_view as tv
        from src.tui.app.trace_view import TraceView
        records = self._records()
        monkeypatch.setattr(tv, "build_trace_records",
                            lambda m: (records, records))
        model = AppModel()
        model.fullscreen = "trace"
        model.trace_help_open = True
        _rec_obj, root = _render_root(TraceView, {"model": model, "width": 120})
        text = _frame_text(_render_frame(root, 120))
        assert "帮助面板" in text
        # 帮助内容为可滚动全量行——窗口只显示前 N 行（内容长度随键位表增长），
        # 键位速查「数据源」应包含全部键位（含 zR）。
        from src.tui.app.trace_view import help_panel_rows
        content = "\n".join(
            "".join(r.text for r in row) for row in help_panel_rows(120)
        )
        assert "zR" in content

    def test_filter_mode_renders_subset(self, monkeypatch):
        from src.tui.app import trace_view as tv
        from src.tui.app.trace_view import TraceView
        records = [
            _rec(1, "hello world"),
            _rec(2, "foo bar"),
            _rec(3, "Hello again"),
        ]
        monkeypatch.setattr(tv, "build_trace_records",
                            lambda m: (records, records))
        model = AppModel()
        model.fullscreen = "trace"
        model.trace_search_pattern = "hello"
        model.trace_search_side = "ledger"
        model.trace_search_matches = [0, 2]
        model.trace_search_idx = 0
        model.trace_search_filter = True
        _rec_obj, root = _render_root(TraceView, {"model": model, "width": 120})
        text = _frame_text(_render_frame(root, 120))
        assert "过滤 2/3 条" in text
        assert "foo bar" not in text


# ═══════════════════════════════════════════════════════════
# 9b. 多键前缀边界 / 面板滚动 / 空数据回退
# ═══════════════════════════════════════════════════════════

class TestKeyPrefixEdges:

    def test_count_buffer_cleared_on_other_key(self, monkeypatch):
        model, handler = _setup_view(monkeypatch, [_rec(1, "a"), _rec(2, "b")],
                                     trace_selected=0)
        handler(_ev("char", "3"))
        assert model.trace_count_buffer == "3"
        handler(_ev("char", "x"))  # 非 g/G → 缓冲清空
        assert model.trace_count_buffer == ""

    def test_z_prefix_requires_following_key(self, monkeypatch):
        model, handler = _setup_view(monkeypatch, [_rec(1, "a")])
        handler(_ev("char", "z"))
        assert model.trace_pending_prefix == "z"
        handler(_ev("char", "q"))  # 非 R/M/a/c/o/C/O → 前缀清除，按键继续处理
        assert model.trace_pending_prefix == ""
        # "q" 无绑定（非帮助面板）→ 放行（handler 返回 False 由模态吞掉）
        assert handler(_ev("char", "q")) is False

    def test_help_panel_scroll(self, monkeypatch):
        long_rec = _rec(1, "x", kind="content",
                        lines=[f"l{i}" for i in range(80)])
        model, handler = _setup_view(monkeypatch, [long_rec],
                                     trace_pane="inspector", trace_selected=0)
        handler(_ev("char", "?"))
        model.trace_inspector_cursor = 0
        assert handler(_ev("char", "j")) is True
        assert model.trace_inspector_cursor == 1
        assert handler(_ev("page_down")) is True
        assert model.trace_inspector_cursor > 1

    def test_empty_meta_parts(self):
        from src.tui.app.trace_view import _meta_parts
        assert _meta_parts(None) == []
        assert _meta_parts(_rec(1, "纯文本")) == []

    def test_slash_closes_stats_panel(self, monkeypatch):
        model, handler = _setup_view(monkeypatch, [_rec(1, "a")])
        assert handler(_ev("char", "i")) is True
        assert model.trace_stats_open is True
        assert handler(_ev("char", "/")) is True
        assert model.trace_stats_open is False
        assert model.trace_search_mode is True

    def test_help_rows_missing_table(self, monkeypatch):
        from src.tui.app import trace_help
        import src.presentation_data as pd
        monkeypatch.setattr(pd, "trace_keymap", lambda: [])
        rows = trace_help.help_panel_rows(30)
        text = "".join(r.text for row in rows for r in row)
        assert "未注册" in text


# ═══════════════════════════════════════════════════════════
# 10. 剪贴板（_screen.set_clipboard OSC52）
# ═══════════════════════════════════════════════════════════

class TestClipboard:

    def test_set_clipboard_writes_osc52(self, monkeypatch):
        import io
        import src.tui._screen as screen
        buf = io.StringIO()
        monkeypatch.setattr(screen.sys, "__stdout__", buf)
        assert screen.set_clipboard("hello") is True
        out = buf.getvalue()
        assert out.startswith("\033]52;c;")
        assert out.endswith("\007")

    def test_set_clipboard_no_tty(self, monkeypatch):
        import src.tui._screen as screen
        monkeypatch.setattr(screen.sys, "__stdout__", None)
        assert screen.set_clipboard("x") is False
