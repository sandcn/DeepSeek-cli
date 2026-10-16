"""轨迹 Trace 检查器原始文本显示（2026-10-09 用户需求）。

需求：**轨迹 Trace 右边（检查器）可以按键切换原始文本显示**——``r`` 键在
「渲染显示」与「原始文本」之间切换：
  - 渲染显示：思考/回答/system/子代理提词走流式 markdown 渲染、工具参数与
    返回值走树形展开（既有行为）；
  - 原始文本：不做 markdown 渲染 / 树解析，直接显示记录原文（markdown 记录
    = 原始 markdown 源码；工具记录 = 原始 arguments 与返回文本）。

覆盖链路：
  1. ``model.trace_raw_text`` 字段（默认 False；``reset_display`` 复位）；
  2. ``_raw_args_text`` —— 参数原文（str 原样 / dict JSON 还原 / None 空串）；
  3. ``_raw_detail_rows`` —— 原始文本行（markdown 原文 / 工具参数与返回值
     原文 + 图片缩略图）；
  4. ``_inspector_content_rows(raw=True)`` —— 原始文本分支（与渲染分支对比、
     keys 全 None、上限截断仍生效）；
  5. ``_inspector_content_deps`` / ``_inspector_deps`` —— raw 参与 deps
     （切换触发重建）且保持既有尾部契约；
  6. ``_inspector_children(raw=True)`` —— 标题标注「原文」；
  7. ``_toggle_raw_text`` / ``r`` 键事件 / 状态行标注 / 帮助面板键位表；
  8. TraceView 端到端渲染（原始文本出现在检查器帧）。
"""

from __future__ import annotations

from types import SimpleNamespace

from src.tui.app.model import AppModel
from src.tui.app.trace_types import TraceRecord
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


def _rec(index=1, summary="记录", **kw):
    base = dict(
        index=index, kind="context", summary=summary, status="",
        time_seconds=None, time_started=None, time_started_monotonic=True,
        tokens={}, result="", lines=[], source_block=None, subagent_label="",
        tool_call_id="", tool_name="", tool_args=None, tool_result="",
        images=[], markdown=False,
    )
    base.update(kw)
    return TraceRecord(**base)


def _joined(rows):
    """检查器行 → 纯文本（str 行原样，StyledRun 行拼 text）。"""
    out = []
    for row in rows:
        if isinstance(row, str):
            out.append(row)
        else:
            out.append("".join(getattr(r, "text", "") for r in row))
    return "\n".join(out)


def _all_str(rows):
    return all(isinstance(r, str) for r in rows)


def _model_with(records, monkeypatch):
    from src.tui.app import trace_view as tv
    monkeypatch.setattr(tv, "build_trace_records", lambda m: (records, records))
    model = AppModel()
    model.fullscreen = "trace"
    return model


def _render_frame(root, width):
    from src.tui.ink import components as _components
    from src.tui.ink.layout import layout_tree
    layout_tree(root, width)
    return _components.render_frame(root, width)


def _frame_text(frame) -> str:
    return "\n".join(
        "".join(r.text for r in line.runs) for line in frame.lines
    )


# ═══════════════════════════════════════════════════════════
# 1. model 字段
# ═══════════════════════════════════════════════════════════

class TestModelField:

    def test_default_off(self):
        model = AppModel()
        assert model.trace_raw_text is False

    def test_reset_display_clears(self):
        model = AppModel()
        model.trace_raw_text = True
        model.reset_display()
        assert model.trace_raw_text is False


# ═══════════════════════════════════════════════════════════
# 2. 参数原文（_raw_args_text）
# ═══════════════════════════════════════════════════════════

class TestRawArgsText:

    def test_none_empty(self):
        from src.tui.app.trace_view import _raw_args_text
        assert _raw_args_text(None) == ""

    def test_str_verbatim(self):
        from src.tui.app.trace_view import _raw_args_text
        src = '{"a":  1,\n  "b": "原样"}'
        assert _raw_args_text(src) == src

    def test_dict_to_json(self):
        from src.tui.app.trace_view import _raw_args_text
        text = _raw_args_text({"a": 1, "中文": "值"})
        assert '"a": 1' in text
        assert "中文" in text

    def test_unserializable_fallback(self):
        from src.tui.app.trace_view import _raw_args_text
        obj = object()
        assert isinstance(_raw_args_text(obj), str)


# ═══════════════════════════════════════════════════════════
# 3. 原始文本行（_raw_detail_rows）
# ═══════════════════════════════════════════════════════════

class TestRawDetailRows:

    def test_markdown_record_keeps_source(self):
        """markdown 记录 → 原文逐行（保留 ``#`` / ``**`` 记号）。"""
        from src.tui.app.trace_view import _raw_detail_rows
        rec = _rec(kind="system", lines=["# 大标题", "", "- 项一", "**加粗**"])
        rows = _raw_detail_rows(rec, 60)
        assert rows
        assert _all_str(rows)
        text = _joined(rows)
        assert "# 大标题" in text
        assert "**加粗**" in text

    def test_tool_record_raw_args_and_result(self):
        """tool 记录 → ▸ 参数 原文 + 分隔线 + ▸ 返回值 原文（无树指示符）。"""
        from src.tui.app.trace_view import _raw_detail_rows
        rec = _rec(
            kind="tool", summary="bash ls",
            tool_args='{"command": "ls -la\\nrm -rf /"}',
            tool_result='{"ok": true}\n第二行',
        )
        rows = _raw_detail_rows(rec, 60)
        text = _joined(rows)
        assert "\u25b8 参数" in text
        assert "\u25b8 返回值" in text
        assert "ls -la" in text
        assert '{"ok": true}' in text
        # 原文模式不做树解析：无展开指示符
        assert "\u25be" not in text
        assert "\u25b8 参数" in text and "\u25b8 返回值" in text

    def test_tool_record_empty_args(self):
        from src.tui.app.trace_view import _raw_detail_rows
        rec = _rec(kind="tool", summary="t", tool_args="", tool_result="out")
        text = _joined(_raw_detail_rows(rec, 60))
        assert "(无参数)" in text
        assert "out" in text

    def test_tool_record_empty_result(self):
        from src.tui.app.trace_view import _raw_detail_rows
        rec = _rec(kind="tool", summary="t", tool_args='{"a": 1}', tool_result="")
        text = _joined(_raw_detail_rows(rec, 60))
        assert "(无返回)" in text

    def test_tool_record_wraps_to_width(self):
        from src.tui.app.trace_view import _raw_detail_rows
        rec = _rec(
            kind="tool", summary="t",
            tool_args='{"command": "' + "x" * 200 + '"}',
            tool_result="y" * 200,
        )
        rows = _raw_detail_rows(rec, 40)
        for row in rows:
            assert len(row if isinstance(row, str) else _joined([row])) <= 40

    def test_tool_record_without_data_falls_back_lines(self):
        """tool 记录无参数/返回值 → 回退 lines 原文。"""
        from src.tui.app.trace_view import _raw_detail_rows
        rec = _rec(kind="tool", summary="t", lines=["调用行", "返回行"])
        text = _joined(_raw_detail_rows(rec, 60))
        assert "调用行" in text and "返回行" in text

    def test_plain_record_wraps(self):
        from src.tui.app.trace_view import _raw_detail_rows
        rec = _rec(kind="user", lines=["abcdef"])
        rows = _raw_detail_rows(rec, 3)
        assert rows == ["abc", "def"]

    def test_subagent_merged_tool_raw_lines(self):
        """合并的 subagent 工具记录 → 追加其原始详情行。"""
        from src.tui.app.trace_view import _raw_detail_rows
        rec = _rec(
            kind="tool", summary="dispatch", subagent_label="agent-1",
            tool_args='{"x": 1}', tool_result="ok",
            lines=["subagent 结果行"],
        )
        text = _joined(_raw_detail_rows(rec, 60))
        assert "subagent 结果行" in text


# ═══════════════════════════════════════════════════════════
# 4. 检查器内容行（_inspector_content_rows raw 分支）
# ═══════════════════════════════════════════════════════════

class TestInspectorContentRowsRaw:

    _MD_LINES = ["# 大标题", "- 项一"]

    def test_raw_markdown_keeps_source(self):
        from src.tui.app.trace_view import _inspector_content_rows
        rec = _rec(kind="system", lines=list(self._MD_LINES), markdown=True)
        rows, keys = _inspector_content_rows(rec, 60, None, True)
        assert rows and _all_str(rows)
        assert keys == [None] * len(rows)
        assert "# 大标题" in _joined(rows)

    def test_rendered_markdown_strips_source(self):
        from src.tui.app.trace_view import _inspector_content_rows
        rec = _rec(kind="system", lines=list(self._MD_LINES), markdown=True)
        rows, _ = _inspector_content_rows(rec, 60, None, False)
        assert rows and not _all_str(rows)  # StyledRun 行
        assert "# 大标题" not in _joined(rows)

    def test_raw_tool_has_no_tree(self):
        from src.tui.app.trace_view import _inspector_content_rows
        rec = _rec(
            kind="tool", summary="t",
            tool_args='{"a": 1}', tool_result='{"b": 2}',
        )
        rows, keys = _inspector_content_rows(rec, 60, None, True)
        assert keys == [None] * len(rows)
        text = _joined(rows)
        assert "\u25b8 参数" in text and "\u25b8 返回值" in text
        # 渲染模式树指示符（▾）在原始模式不出现
        assert "\u25be" not in text

    def test_raw_mode_max_rows_guard(self):
        """原始模式同样受内容上限保护（超限截断 + 提示行）。"""
        from src.tui.app.trace_view import (
            _INSPECTOR_MAX_ROWS, _inspector_content_rows,
        )
        rec = _rec(kind="user", lines=[f"line{i}" for i in range(_INSPECTOR_MAX_ROWS + 50)])
        rows, keys = _inspector_content_rows(rec, 60, None, True)
        assert len(rows) <= _INSPECTOR_MAX_ROWS + 1
        assert "\u2026 内容过长" in _joined(rows[-1:])

    def test_raw_default_false_unchanged(self):
        """省略 raw 参数 → 渲染形态（向后兼容）。"""
        from src.tui.app.trace_view import _inspector_content_rows
        rec = _rec(kind="system", lines=list(self._MD_LINES), markdown=True)
        a, _ = _inspector_content_rows(rec, 60)
        b, _ = _inspector_content_rows(rec, 60, None, False)
        assert _joined(a) == _joined(b)


# ═══════════════════════════════════════════════════════════
# 5. deps（raw 参与 + 尾部契约）
# ═══════════════════════════════════════════════════════════

class TestDepsRaw:

    def test_content_deps_raw_changes(self):
        from src.tui.app.trace_view import _inspector_content_deps
        rec = _rec(kind="system", lines=["# a"], markdown=True)
        d0 = _inspector_content_deps(rec, 60, None, False)
        d1 = _inspector_content_deps(rec, 60, None, True)
        assert d0 != d1
        assert all(isinstance(x, (int, str)) or x is None for x in d0)
        assert all(isinstance(x, (int, str)) or x is None for x in d1)
        # 末位仍为折叠集合展平串（既有契约）
        assert d0[-1] == "" and d1[-1] == ""

    def test_inspector_deps_raw_changes(self):
        from src.tui.app.trace_view import _inspector_deps
        rec = _rec(kind="system", lines=["# a"])
        d0 = _inspector_deps(rec, 40, 24, 0, -1, False, False)
        d1 = _inspector_deps(rec, 40, 24, 0, -1, False, True)
        assert d0 != d1
        # 末两位仍为 scroll/cursor（既有调用面契约）
        assert d0[-2] == 0 and d0[-1] == -1


# ═══════════════════════════════════════════════════════════
# 6. 检查器元素树标题标注（_inspector_children raw）
# ═══════════════════════════════════════════════════════════

class TestInspectorChildrenRaw:

    def _title(self, children):
        return str(children[0].props.get("children", ""))

    def test_raw_title_tag(self):
        from src.tui.app.trace_view import _inspector_children
        rec = _rec(kind="system", lines=["# a"])
        raw_children = _inspector_children(rec, 40, 20, raw=True)
        plain_children = _inspector_children(rec, 40, 20, raw=False)
        assert "原文" in self._title(raw_children)
        assert "原文" not in self._title(plain_children)

    def test_raw_content_in_frame(self):
        from src.tui.app.trace_view import _inspector_children
        rec = _rec(kind="system", lines=["# 大标题"], markdown=True)
        children = _inspector_children(rec, 40, 20, raw=True)
        texts = [str(c.props.get("children", "")) for c in children]
        assert "# 大标题" in texts


# ═══════════════════════════════════════════════════════════
# 7. 切换（_toggle_raw_text / r 键 / 状态行 / 键位表）
# ═══════════════════════════════════════════════════════════

class TestToggle:

    def test_toggle_flips_and_messages(self):
        from src.tui.app.trace_view import _toggle_raw_text
        model = AppModel()
        assert _toggle_raw_text(model) is True
        assert model.trace_raw_text is True
        assert "原始文本" in model.trace_status_message
        assert _toggle_raw_text(model) is False
        assert model.trace_raw_text is False
        assert "渲染" in model.trace_status_message

    def test_toggle_clears_inspector_search(self):
        from src.tui.app.trace_view import _toggle_raw_text
        model = AppModel()
        model.trace_search_side = "inspector"
        model.trace_search_pattern = "abc"
        model.trace_search_matches = [1, 2]
        model.trace_search_idx = 0
        _toggle_raw_text(model)
        assert model.trace_search_pattern == ""
        assert model.trace_search_matches == []
        assert model.trace_search_idx == -1

    def test_toggle_keeps_ledger_search(self):
        from src.tui.app.trace_view import _toggle_raw_text
        model = AppModel()
        model.trace_search_side = "ledger"
        model.trace_search_pattern = "abc"
        model.trace_search_matches = [1]
        _toggle_raw_text(model)
        assert model.trace_search_pattern == "abc"
        assert model.trace_search_matches == [1]

    def test_status_line_marks_raw(self):
        from src.tui.app.trace_view import _status_line_text
        model = AppModel()
        assert "原文" not in _status_line_text(model)
        model.trace_raw_text = True
        assert "原文" in _status_line_text(model)

    def test_keymap_entry(self):
        from src.presentation_data import trace_keymap
        rows = trace_keymap()
        assert any(r.get("keys") == "r" and "原始文本" in r.get("desc", "")
                   for r in rows)

    def test_r_key_handler(self, monkeypatch):
        from src.tui.app.trace_view import TraceView
        records = [_rec(1, "hello")]
        model = _model_with(records, monkeypatch)
        _rec_obj, root = _render_root(TraceView, {"model": model, "width": 120})
        handler = _find_input_handler(root)
        assert handler is not None
        assert handler(_ev("char", "r")) is True
        assert model.trace_raw_text is True
        assert handler(_ev("char", "r")) is True
        assert model.trace_raw_text is False


# ═══════════════════════════════════════════════════════════
# 8. 端到端渲染（TraceView 帧）
# ═══════════════════════════════════════════════════════════

class TestEndToEnd:

    def test_frame_shows_raw_source_after_toggle(self, monkeypatch):
        from src.tui.app.trace_view import TraceView
        records = [_rec(1, "提词", kind="system", lines=["# 大标题"], markdown=True)]
        model = _model_with(records, monkeypatch)
        model.trace_selected = 0
        _rec_obj, root = _render_root(TraceView, {"model": model, "width": 120})
        text = _frame_text(_render_frame(root, 120))
        assert "# 大标题" not in text
        # 打开原始文本显示 → 新渲染（全新 Reconciler，无 memo 干预）
        model.trace_raw_text = True
        _rec_obj2, root2 = _render_root(TraceView, {"model": model, "width": 120})
        text2 = _frame_text(_render_frame(root2, 120))
        assert "# 大标题" in text2
        assert "原文" in text2
