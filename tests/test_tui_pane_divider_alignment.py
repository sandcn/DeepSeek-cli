"""TUI 双栏分隔线 / 显示宽度对齐 / 值驱动缓存修复回归测试。

覆盖 2026-10 review 批次修复：

1. ``pane_divider``——双栏视图的竖直分隔线填充到栏高（修复前 ``height=1``
   的 TEXT 只渲染首行）；
2. ``pad_to_width``——按显示宽度（CJK/emoji 计 2 列）对齐，替代按字符数
   填充的 f-string（``{label:<N}``）；
3. ``usage_view`` 标签列宽取「最大标签显示宽度」→ 值列全表对齐；
4. ``status_runs`` 按显示宽度截断；
5. ``build_header_runs`` 超宽经省略号截断（不再拦腰切断操作提示）；
6. ``changes_view._detail_deps`` 值驱动（替代 ``id(entry)`` 缓存键）；
7. ``export_view`` 选中「执行导出」不再渲染双箭头。
"""

from __future__ import annotations

import re

import pytest

from src._text_width import string_width
from src.tui.app._view_common import (
    build_header_runs,
    pad_to_width,
    pane_divider,
    status_runs,
)
from src.tui.ink import h, renderToString

ANSI = re.compile(r"\x1b\[[0-9;?]*[@-~]")


def _lines(out: str) -> list:
    return [ANSI.sub("", ln) for ln in out.split("\n")]


# ── pane_divider ───────────────────────────────────────

def test_pane_divider_fills_all_rows():
    out = renderToString(pane_divider(5, None), {"columns": 10})
    lines = out.split("\n")
    assert len(lines) == 5
    assert all(line.strip() == "\u2502" for line in lines)


def test_pane_divider_min_one_row_and_bad_input():
    assert renderToString(pane_divider(0, None), {"columns": 10}).split("\n") == ["\u2502"]
    assert renderToString(pane_divider(-3, None), {"columns": 10}).split("\n") == ["\u2502"]
    assert renderToString(pane_divider("bad", None), {"columns": 10}).split("\n") == ["\u2502"]


_TWO_PANE_VIEWS = "sessions changes theme skill mcp search outline keymap notify".split()


def _view_classes():
    from src.tui.app.changes_view import ChangesView
    from src.tui.app.keymap_view import KeymapView
    from src.tui.app.mcp_view import McpView
    from src.tui.app.notify_view import NotifyView
    from src.tui.app.outline_view import OutlineView
    from src.tui.app.search_view import SearchView
    from src.tui.app.sessions_view import SessionsView
    from src.tui.app.skill_view import SkillView
    from src.tui.app.theme_view import ThemeView

    return {
        "sessions": SessionsView, "changes": ChangesView, "theme": ThemeView,
        "skill": SkillView, "mcp": McpView, "search": SearchView,
        "outline": OutlineView, "keymap": KeymapView, "notify": NotifyView,
    }


@pytest.mark.parametrize("view_id", _TWO_PANE_VIEWS)
def test_two_pane_views_divider_spans_multiple_rows(view_id):
    """双栏视图：右栏多行内容时分隔线每行都出现（修复前仅首行）。"""
    from src.tui.app.model import AppModel
    from tests.test_tui_new_views import _case_state

    classes = _view_classes()
    case = None
    for attr, state, _a, _b, _c in _case_state():
        if attr == f"{view_id}_view":
            case = (attr, state)
            break
    assert case is not None, view_id
    attr, state = case
    entries = getattr(state, "entries", None)
    if entries:
        # 复制条目到多行（左右栏均多行，分隔线需覆盖全部行）
        setattr(state, "entries", [dict(e) for e in entries] * 6)
    model = AppModel()
    setattr(model, attr, state)
    out = renderToString(
        h(classes[view_id], {"model": model, "width": 100}), {"columns": 100},
    )
    bar_rows = [i for i, ln in enumerate(_lines(out)) if "\u2502" in ln]
    assert len(bar_rows) >= 3, (view_id, bar_rows)


# ── pad_to_width ───────────────────────────────────────

def test_pad_to_width_uses_display_width():
    assert string_width(pad_to_width("格式", 8)) == 8
    assert string_width(pad_to_width("abcd", 8)) == 8
    # 已达目标列宽 → 仍保证 min_pad 个间隔空格
    assert string_width(pad_to_width("执行导出", 8)) == 9
    assert string_width(pad_to_width("执行导出", 8, min_pad=0)) == 8
    assert pad_to_width("合计", 14).endswith(" " * 10)


def test_pad_to_width_oversize_label_keeps_min_gap():
    wide = "超长中文标签内容"
    padded = pad_to_width(wide, 8)
    assert padded == wide + " "
    assert pad_to_width("x", 0) == "x "
    assert pad_to_width("x", 0, min_pad=0) == "x"


# ── usage_view 对齐 ────────────────────────────────────

def test_usage_label_column_uses_max_display_width():
    from src.tui.app.usage_view import _label_column

    assert _label_column([{"title": "T", "rows": [("合计", "0")]}]) == 14
    sections = [{"title": "T", "rows": [("输入（未命中）", "0"), ("合计", "0")]}]
    # "输入（未命中）" = 7 个 CJK = 14 显示列 → 列宽 14 + 1（间隔）
    assert _label_column(sections) == 15
    assert _label_column(None) == 14


def test_usage_content_rows_values_aligned():
    from src.tui.app.usage_view import _content_rows

    sections = [{
        "title": "用量",
        "rows": [("输入（未命中）", "0t"), ("合计", "0t")],
    }]
    starts = []
    for runs in _content_rows(sections, 100):
        text = "".join(r.text for r in runs)
        if "0t" in text:
            starts.append(string_width(text.split("0t")[0]))
    assert len(starts) == 2
    assert len(set(starts)) == 1, starts


# ── status_runs / build_header_runs ────────────────────

def test_status_runs_truncates_by_display_width():
    runs = status_runs([], style=None, message="中文" * 60, width=40)
    assert runs is not None
    assert string_width(runs[0].text) <= 40
    assert string_width(runs[0].text) > 0


def test_build_header_runs_ellipsis_on_overflow():
    from src.tui.core.style import Style

    runs = build_header_runs(
        "标题", Style(), [" · 1"], "  " + "x" * 300, 40,
        hint_style=Style(), sep_style=Style(),
    )
    text = "".join(r.text for r in runs)
    assert "\u2026" in text
    assert string_width(text) <= 40


def test_build_header_runs_no_ellipsis_when_fits():
    from src.tui.core.style import Style

    runs = build_header_runs(
        "T", Style(), [" · 1"], "  hint", 40,
        hint_style=Style(), sep_style=Style(),
    )
    text = "".join(r.text for r in runs)
    assert "\u2026" not in text
    assert string_width(text) == 40


# ── changes_view 值驱动 deps ───────────────────────────

def test_changes_detail_deps_value_driven():
    from src.tui.app.changes_view import _detail_deps

    base = {
        "path": "a.py", "change_label": "修改", "records": 1,
        "message_index": "0-1", "before": "old\n", "after": "new\n",
    }
    same = dict(base)
    assert _detail_deps(base, 60) == _detail_deps(same, 60)
    changed = dict(base, after="new2\n")
    assert _detail_deps(base, 60) != _detail_deps(changed, 60)
    renamed = dict(base, path="b.py")
    assert _detail_deps(base, 60) != _detail_deps(renamed, 60)
    assert _detail_deps(base, 60) != _detail_deps(base, 40)
    assert _detail_deps(None, 60) == (None, 60)


def test_changes_view_memo_recomputes_on_entry_replacement():
    """entry 对象被替换（回滚后重建）且内容不同 → 预览重算。"""
    from src.tui.app import _state_types as st
    from src.tui.app.changes_view import ChangesView
    from src.tui.app.model import AppModel

    model = AppModel()
    state = st.ChangesViewState(visible=True, seq=1, entries=[{
        "path": "a.py", "change_label": "修改", "records": 1,
        "message_index": "0-1", "before": "old\n", "after": "NEW-A\n",
    }])
    model.changes_view = state
    first = renderToString(h(ChangesView, {"model": model, "width": 100}),
                           {"columns": 100})
    assert "NEW-A" in ANSI.sub("", first)
    state.entries = [{
        "path": "a.py", "change_label": "修改", "records": 1,
        "message_index": "0-1", "before": "old\n", "after": "NEW-B\n",
    }]
    state.seq += 1
    second = renderToString(h(ChangesView, {"model": model, "width": 100}),
                            {"columns": 100})
    assert "NEW-B" in ANSI.sub("", second)


# ── logs_view 一致性统计标签对齐 ────────────────────────

def test_logs_verify_stats_values_aligned():
    from src.tui.app import _state_types as st
    from src.tui.app.logs_view import _verify_rows

    state = st.LogsViewState(visible=True, seq=1)
    state.verify_ok = True
    state.verify_text = "ok"
    state.stats = [("事件数", "2"), ("MESSAGES", "5"), ("很长的中文统计标签内容", "9")]
    rows = _verify_rows(state, 80)
    starts = []
    for runs in rows:
        text = "".join(r.text for r in runs)
        for value, marker in (("2", "事件数"), ("5", "MESSAGES"), ("9", "很长的中文统计标签内容")):
            if marker in text and text.rstrip().endswith(value):
                starts.append(string_width(text[: text.rindex(value)]))
    assert len(starts) == 3
    assert len(set(starts)) == 1, starts


# ── export_view 双箭头 ─────────────────────────────────

def test_export_view_selected_action_has_single_arrow():
    from src.tui.app import _state_types as st
    from src.tui.app.export_view import ExportView
    from src.tui.app.model import AppModel

    model = AppModel()
    state = st.ExportViewState(visible=True, seq=1, format="md")
    state.selected = 3
    model.export_view = state
    out = renderToString(h(ExportView, {"model": model, "width": 96}),
                         {"columns": 96})
    rows = [ln for ln in _lines(out) if "执行导出" in ln]
    assert len(rows) == 1
    assert rows[0].count("\u25b6") == 1, rows[0]


def test_export_view_labels_aligned():
    from src.tui.app import _state_types as st
    from src.tui.app.export_view import ExportView
    from src.tui.app.model import AppModel

    model = AppModel()
    state = st.ExportViewState(visible=True, seq=1, format="md")
    state.selected = -1  # 无选中行：标签列即 8 显示列
    model.export_view = state
    out = renderToString(h(ExportView, {"model": model, "width": 96}),
                         {"columns": 96})
    fmt_row = [ln for ln in _lines(out) if "格式" in ln][0]
    action_row = [ln for ln in _lines(out) if "执行导出" in ln][0]
    # 值列起点：从缩进后计算到值文本的显示宽度
    assert string_width(fmt_row.split("md")[0]) == string_width(
        action_row.split("(Enter 执行)")[0]
    ) - string_width("   ")
