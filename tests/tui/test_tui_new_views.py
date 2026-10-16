"""新增全屏视图批次（sessions/changes/theme/skill/mcp/usage/search/
keymap/notify/export）单元测试。

覆盖视图注册、状态基类语义、纯函数逻辑与命令辅助（不依赖真实 ChatUI）。
"""

from __future__ import annotations

import types

import pytest


@pytest.fixture(autouse=True)
def _clean_view_registry():
    import src.tui.app.view_registry as vreg

    vreg.reset()
    yield
    vreg.reset()


# ── 视图注册 ──────────────────────────────────────────

def test_new_views_registered():
    from src.tui.app.view_registry import active_view_ids, fullscreen_views

    ids = active_view_ids()
    for view_id in (
        "sessions", "changes", "theme", "skill", "mcp", "usage",
        "search", "keymap", "notify", "export",
    ):
        assert view_id in ids
        assert view_id in fullscreen_views(), view_id


def test_manifest_declares_new_ui_views():
    from src.plugins.manifest import UI_VIEW_ENTRIES

    ids = {e["config"]["id"] for e in UI_VIEW_ENTRIES}
    for view_id in ("sessions", "changes", "theme", "skill", "mcp",
                    "usage", "search", "keymap", "notify", "export"):
        assert view_id in ids


# ── 状态基类 ──────────────────────────────────────────

def test_list_view_state_final_first_write_wins():
    from src.tui.app._state_types import ListViewState

    state = ListViewState()
    assert state.try_set_final("cancel") is True
    assert state.done is True
    assert state.action == "cancel"
    assert state.try_set_final("timeout") is False
    assert state.action == "cancel"


def test_view_states_inherit_base():
    from src.tui.app._state_types import (
        ChangesViewState, ExportViewState, KeymapViewState, ListViewState,
        McpViewState, NotifyViewState, SearchViewState,
        SessionsViewState, SkillViewState, ThemeViewState, UsageViewState,
    )

    for cls in (
        SessionsViewState, ChangesViewState, ThemeViewState, SkillViewState,
        McpViewState, UsageViewState, SearchViewState,
        KeymapViewState, NotifyViewState, ExportViewState,
    ):
        assert issubclass(cls, ListViewState)


def test_model_has_new_view_states():
    from src.tui.app.model import AppModel
    from src.tui.app._state_types import ListViewState

    model = AppModel()
    for attr in (
        "sessions_view", "changes_view", "theme_view", "skill_view",
        "mcp_view", "usage_view", "search_view",
        "keymap_view", "notify_view", "export_view",
    ):
        assert isinstance(getattr(model, attr), ListViewState), attr


# ── view opener ───────────────────────────────────────

def test_open_fullscreen_view_no_chat_ui_returns_false():
    from src.core.commands._view_opener import open_fullscreen_view
    from src.tui.app._state_types import SessionsViewState

    ctx = types.SimpleNamespace()
    opened = open_fullscreen_view(
        ctx, view_id="sessions", state_attr="sessions_view",
        state_cls=SessionsViewState,
    )
    assert opened is False


# ── ANSI 解析 ─────────────────────────────────────────

def test_ansi_runs_parse_sgr():
    from src.tui.app._ansi_runs import ansi_line_to_runs

    runs = ansi_line_to_runs("\x1b[31mred\x1b[0m plain")
    assert "".join(r.text for r in runs) == "red plain"
    assert runs[0].style is not None and runs[0].style.fg == 1


def test_ansi_runs_truecolor_and_empty():
    from src.tui.app._ansi_runs import ansi_line_to_runs

    assert ansi_line_to_runs("") == []
    runs = ansi_line_to_runs("\x1b[38;2;10;20;30mX")
    assert runs and runs[0].style is not None


# ── sessions ──────────────────────────────────────────

def test_session_search_text():
    from src.tui.app.sessions_view import _session_search_text

    text = _session_search_text({"title": "T", "id": "abc", "model": "m1"})
    assert "T" in text and "abc" in text and "m1" in text


def test_session_preview_and_entries():
    from src.core.commands._data_cmd import _build_session_entries, _session_preview

    preview = _session_preview({"messages": [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": [{"type": "text", "text": "world"}]},
    ]})
    assert preview[0][0] == "用户" and "hello" in preview[0][1]
    assert preview[1][1] == "world"

    class _P:
        def list_sessions(self):
            return [{"id": "a", "title": "t", "model": "m", "saved_at": "x",
                     "message_count": 1}]

        def load_session(self, sid):
            return {"messages": [{"role": "user", "content": "hi"}]}

    entries = _build_session_entries(_P())
    assert entries and entries[0]["preview_lines"][0][1] == "hi"


# ── changes ───────────────────────────────────────────

def test_change_entries_and_detail_rows():
    from src.core.commands._session_cmd import _build_change_entries
    from src.tui.app.changes_view import _change_search_text, _detail_rows

    class _Rec:
        def __init__(self, path, before, after, idx=0):
            self.file_path = path
            self.content_before = before
            self.content_after = after
            self.message_index = idx

    class _Sandbox:
        def get_all_file_changes(self):
            return [_Rec("a.py", "old\n", "new\n", 1), _Rec("b.py", None, "x", 2)]

    entries = _build_change_entries(_Sandbox())
    labels = {e["path"]: e["change_label"] for e in entries}
    assert labels["a.py"] == "修改"
    assert labels["b.py"] == "新建"

    rows = _detail_rows(entries[0], 60)
    assert rows
    assert "a.py" in _change_search_text(entries[0])


# ── theme ─────────────────────────────────────────────

def test_theme_preview_rows():
    from src.tui.app.theme_view import _palette_preview_rows

    rows = _palette_preview_rows("dark", 40)
    assert rows and any("accent" in "".join(r.text for r in row) for row in rows)


# ── keymap ────────────────────────────────────────────

def test_keybinding_combo_conversion():
    from src.tui._keybindings import combo_to_key, key_to_combo

    assert combo_to_key("ctrl+k") == "\x0b"
    assert combo_to_key("^g") == "\x07"
    assert combo_to_key("ctrl+/") == "\x1f"
    assert combo_to_key("ctrl+space") == "\x00"
    assert combo_to_key("garbage") is None
    assert key_to_combo("\x0b") == "ctrl+k"
    assert key_to_combo("\x1f") == "ctrl+/"


def test_list_keybindings_adapter():
    from src.core.adapters.ui_runtime import list_keybindings

    entries = list_keybindings()
    assert entries
    assert all({"id", "combo", "action"} <= set(e) for e in entries)


# ── notify ────────────────────────────────────────────

def test_notify_history_record_clear():
    from src.notifications import history

    history.clear()
    history.record("error", "boom", "detail", level="error")
    entries = history.entries()
    assert len(entries) == 1 and entries[0]["title"] == "boom"
    history.clear()
    assert history.entries() == []


def test_notify_format_time_and_search_text():
    from src.tui.app.notify_view import _notify_search_text, format_time

    assert len(format_time(0)) == 8
    text = _notify_search_text({"title": "t", "body": "b", "kind": "error"})
    assert "t" in text and "b" in text


# ── search ────────────────────────────────────────────

def test_search_messages():
    from src.tui.app.search_view import search_messages

    messages = [
        {"index": 0, "role": "user", "text": "hello world"},
        {"index": 1, "role": "assistant", "text": "no match"},
    ]
    results = search_messages(messages, "WORLD")
    assert len(results) == 1 and results[0]["msg_index"] == 0
    assert search_messages(messages, "") == []


# ── export ────────────────────────────────────────────

def test_export_resolve_path(tmp_path, monkeypatch):
    from src.core.commands import _export_cmd

    monkeypatch.chdir(tmp_path)
    path, err = _export_cmd._resolve_export_path("", "json")
    assert path is not None and err == "" and path.suffix == ".json"
    bad, err2 = _export_cmd._resolve_export_path("../outside.txt", "md")
    assert bad is None and err2


# ── usage ─────────────────────────────────────────────

def test_build_usage_sections():
    from src.core.commands._usage_cmd import build_usage_sections

    ctx = types.SimpleNamespace(state={"model": "m"}, messages=[], config_port=None)
    sections = build_usage_sections(ctx)
    titles = [s["title"] for s in sections]
    assert "Token 用量" in titles and "上下文窗口" in titles and "费用估算" in titles


# ── mcp / skill 命令辅助 ──────────────────────────────

def test_mcp_build_entries_no_servers():
    from src.core.commands._mcp_cmd import _build_mcp_entries

    assert isinstance(_build_mcp_entries(), list)


def test_skill_build_entries_returns_list():
    from src.core.commands._skill_cmd import _build_skill_entries

    assert isinstance(_build_skill_entries(), list)


# ── 端到端渲染（以 renderToString 驱动真实组件树） ─────────

def _render_view(view_cls, model, width=100):
    from src.tui.ink import h, renderToString

    return renderToString(h(view_cls, {"model": model, "width": width}),
                          {"columns": width})


def _case_state():
    """构造各视图的可见状态与期望文本。"""
    from src.tui.app import _state_types as st

    return [
        ("sessions_view", st.SessionsViewState(visible=True, seq=1, entries=[
            {"id": "abc", "title": "示例会话", "model": "m", "saved_at": "now",
             "message_count": 2, "preview_lines": [("用户", "hi")]},
        ]), "sessions_view", "会话浏览器", "示例会话"),
        ("changes_view", st.ChangesViewState(visible=True, seq=1, entries=[
            {"path": "a.py", "change_label": "修改", "before": "old\n",
             "after": "new\n", "records": 1, "message_index": "0-1"},
        ]), "changes_view", "文件变更审查", "a.py"),
        ("theme_view", st.ThemeViewState(visible=True, seq=1, entries=[
            {"name": "dark", "desc": "暗色", "active": True},
        ]), "theme_view", "主题选择器", "dark"),
        ("skill_view", st.SkillViewState(visible=True, seq=1, entries=[
            {"name": "demo", "description": "d", "source": "s", "provider": "p",
             "model_invocable": True, "user_invocable": True},
        ]), "skill_view", "技能浏览器", "demo"),
        ("mcp_view", st.McpViewState(visible=True, seq=1, entries=[
            {"name": "srv", "transport": "stdio", "enabled": True,
             "connected": True, "tools": ["t1"], "agents": ["main"]},
        ]), "mcp_view", "MCP 服务器", "srv"),
        ("usage_view", st.UsageViewState(visible=True, seq=1, sections=[
            {"title": "Token 用量", "rows": [("合计", "10t", "info", None)]},
        ]), "usage_view", "用量仪表盘", "Token 用量"),
        ("search_view", st.SearchViewState(visible=True, seq=1, messages=[
            {"index": 0, "role": "user", "text": "hello"}],
            search_pattern="hello", results=[
                {"msg_index": 0, "role": "user", "snippet": "hello"}],
        ), "search_view", "对话内搜索", "hello"),
        ("keymap_view", st.KeymapViewState(visible=True, seq=1, entries=[
            {"id": "ctrl_g", "key": "\x07", "combo": "ctrl+g", "default_combo": "ctrl+g",
             "action": "vim", "description": "打开 vim"},
        ]), "keymap_view", "键位编辑器", "ctrl+g"),
        ("notify_view", st.NotifyViewState(visible=True, seq=1, entries=[
            {"seq": 1, "kind": "error", "title": "boom", "body": "x",
             "level": "error", "time": 0},
        ]), "notify_view", "通知 / 事件日志", "boom"),
        ("export_view", st.ExportViewState(visible=True, seq=1, format="md"), "export_view", "导出向导", "格式"),
    ]


def test_all_new_views_render_visible():
    from src.tui.app.changes_view import ChangesView
    from src.tui.app.export_view import ExportView
    from src.tui.app.keymap_view import KeymapView
    from src.tui.app.mcp_view import McpView
    from src.tui.app.model import AppModel
    from src.tui.app.notify_view import NotifyView
    from src.tui.app.search_view import SearchView
    from src.tui.app.sessions_view import SessionsView
    from src.tui.app.skill_view import SkillView
    from src.tui.app.theme_view import ThemeView
    from src.tui.app.usage_view import UsageView

    classes = {
        "sessions_view": SessionsView, "changes_view": ChangesView,
        "theme_view": ThemeView, "skill_view": SkillView, "mcp_view": McpView,
        "usage_view": UsageView, "search_view": SearchView,
        "keymap_view": KeymapView,
        "notify_view": NotifyView, "export_view": ExportView,
    }
    for attr, state, _a, need_title, need_content in _case_state():
        model = AppModel()
        setattr(model, attr, state)
        out = _render_view(classes[attr], model)
        assert need_title in out, (attr, need_title)
        assert need_content in out, (attr, need_content)


def test_all_new_views_invisible_render_empty():
    from src.tui.app.model import AppModel
    from src.tui.app.sessions_view import SessionsView

    model = AppModel()  # sessions_view 默认不可见
    out = _render_view(SessionsView, model)
    assert "会话浏览器" not in out


# ── /outline 移除回归 ─────────────────────────────────

def test_outline_feature_removed():
    """/outline 功能已彻底移除（命令 / 视图 / 状态 / 桥接 / 清单条目）。"""
    import importlib.util

    import src.tui.app._state_types as st
    from src.core.adapters import ui_runtime
    from src.plugins.manifest import COMMAND_PLUGIN_ENTRIES, UI_VIEW_ENTRIES
    from src.tui.app.model import AppModel
    from src.tui.app.view_registry import active_view_ids, fullscreen_views

    assert "outline" not in active_view_ids()
    assert "outline" not in fullscreen_views()
    assert not hasattr(st, "OutlineViewState")
    assert not hasattr(AppModel(), "outline_view")
    assert not hasattr(ui_runtime, "get_outline_view_state_cls")
    assert "outline" not in {e["config"]["id"] for e in UI_VIEW_ENTRIES}
    assert "outline" not in {e["config"]["name"] for e in COMMAND_PLUGIN_ENTRIES}
    assert importlib.util.find_spec("src.core.commands._outline_cmd") is None
    assert importlib.util.find_spec("src.tui.app.outline_view") is None
