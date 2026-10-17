"""命令面板（F2）+ 视图内命令执行 / 填入 + 工作区状态段单元测试。

覆盖：
  - 命令目录构建（去重 / exit / 分组标签）；
  - 子序列模糊匹配、最近使用优先、多关键词 AND；
  - 左栏行 / 右栏详情纯函数；
  - 可见/不可见渲染与输入路由（过滤 → Enter 执行 / Tab 填入）；
  - F2 分发回调与视图注册表 / 清单声明；
  - 帮助速查视图 Enter 执行 / Tab 填入命令；
  - ``Input.submit_text`` / ``Input.prefill_text`` 提交语义；
  - 状态栏工作区段（分支 + 目录）。
"""

from __future__ import annotations

import os
from types import SimpleNamespace

from src.tui._input_parser import KeyEvent
from src.tui.core.style import Style
from src.tui.app._command_actions import (
    command_text,
    recent_commands,
    remember_recent,
    run_command,
)
from src.tui.app.command_palette import (
    _detail_rows,
    _left_row,
    _match_entry,
    _palette_entries,
    _rank_entries,
)
from src.tui.app.model import AppModel
from tests.tui.ink._harness import Harness


def _register_all_commands():
    import importlib

    for _mod in (
        "src.core.commands._config_cmd",
        "src.core.commands._data_cmd",
        "src.core.commands._export_cmd",
        "src.core.commands._plugin_cmd",
        "src.core.commands._session_cmd",
        "src.core.commands.plugins.deitmsg_plugin",
        "src.core.commands.plugins.editmsg_plugin",
        "src.core.commands.plugins.loop_plugin",
        "src.core.commands.plugins.model_plugin",
        "src.core.commands.plugins.skill_plugin",
    ):
        importlib.import_module(_mod)
    from src.core.commands.base import (
        command_registry_singleton,
        declared_command_names,
        declared_command_plugin,
    )

    reg = command_registry_singleton()
    for name in declared_command_names():
        reg.register(declared_command_plugin(name))
    return reg


def _text(runs) -> str:
    return "".join(r.text for r in runs)


# ── 命令目录 / 模糊匹配 ──────────────────────────────────

def test_palette_entries_unique_and_contain_exit():
    _register_all_commands()
    entries = _palette_entries()
    names = [entry["name"] for entry in entries]
    assert "exit" in names
    assert len(names) == len(set(names))
    assert "theme" in names and "help" in names
    theme = next(entry for entry in entries if entry["name"] == "theme")
    assert theme["group"] == "ui"
    assert theme["group_label"]


def test_fuzzy_rank_prefers_command_name_prefix():
    entries = [
        {"name": "theme", "aliases": (), "group": "ui", "group_label": "界面与配置", "desc": "切换配色主题"},
        {"name": "help", "aliases": (), "group": "ui", "group_label": "界面与配置", "desc": "显示帮助"},
    ]
    ranked = _rank_entries(entries, "th", [])
    assert ranked[0][0]["name"] == "theme"
    assert ranked[0][1]["name"]  # 命中位置高亮


def test_fuzzy_matches_alias_and_multi_token():
    entry = {
        "name": "models", "aliases": ("mdl",), "group": "model",
        "group_label": "模型与推理", "desc": "模型档案选择器",
    }
    matched = _match_entry(entry, "mdl")
    assert matched is not None
    assert "alias" in matched[1]
    matched = _match_entry(entry, "模型 档案")
    assert matched is not None
    assert "desc" in matched[1]


def test_recent_entries_rank_first_when_query_empty():
    entries = [
        {"name": "a", "aliases": (), "group": "g", "group_label": "G", "desc": ""},
        {"name": "b", "aliases": (), "group": "g", "group_label": "G", "desc": ""},
    ]
    ranked = _rank_entries(entries, "", ["b"])
    assert [entry["name"] for entry, _ in ranked] == ["b", "a"]


# ── 渲染纯函数 ──────────────────────────────────────────

def test_left_row_selected_fills_width_and_highlights():
    entry = {"name": "theme", "aliases": ("th",), "group": "ui", "group_label": "界面与配置", "desc": "切换配色主题"}
    styles = {
        "accent": Style(fg=45, bold=True),
        "recent": Style(fg=214),
        "name": Style(fg=252),
        "desc": Style(fg=242),
        "match": Style(fg=45, underline=True),
        "sel_bg": Style(bg=237),
    }
    _, highlights = _match_entry(entry, "th")
    runs = _left_row(entry, highlights, True, True, 40, styles)
    assert sum(run.width for run in runs) == 40
    assert all(run.style is not None and run.style.bg == 237 for run in runs)
    assert "/theme" in _text(runs)


def test_detail_rows_include_meta_and_action():
    entry = {"name": "theme", "aliases": ("th",), "group": "ui", "group_label": "界面与配置", "desc": "切换配色主题"}
    rows = _detail_rows(entry, [entry], 60, {
        "title": Style(fg=45, bold=True),
        "accent": Style(fg=45, bold=True),
        "name": Style(fg=252),
        "desc": Style(fg=242),
        "group": Style(fg=110),
        "recent": Style(fg=214),
        "hint": Style(fg=242),
        "sep": Style(fg=238),
        "warn": Style(fg=214, bold=True),
    }, recent=True, has_draft=True)
    text = "\n".join(_text(row) for row in rows)
    assert "/theme" in text and "界面与配置" in text
    assert "Enter" in text and "Tab" in text
    assert "草稿" in text


# ── 组件渲染 / 输入路由 ─────────────────────────────────

def test_palette_view_registered_and_declared():
    import src.tui.app.view_registry as vreg
    from src.plugins.manifest import UI_VIEW_ENTRIES

    assert "palette" in vreg.builtin_view_ids()
    assert "palette" in vreg.active_view_ids()
    assert "palette" in {entry["config"].get("id") for entry in UI_VIEW_ENTRIES}


def test_palette_render_visible_and_invisible():
    from src.tui.app.command_palette import CommandPalette
    from src.tui.ink import h, renderToString

    model = AppModel()
    model.fullscreen = "palette"
    out = renderToString(
        h(CommandPalette, {"model": model, "width": 100}), {"columns": 100}
    )
    assert "命令面板" in out
    assert "/theme" in out
    assert "Enter 执行" in out

    model.fullscreen = ""
    out = renderToString(
        h(CommandPalette, {"model": model, "width": 100}), {"columns": 100}
    )
    assert "命令面板" not in out


def test_palette_filters_and_executes_through_router():
    from src.tui.app.command_palette import CommandPalette
    from src.tui.ink import h

    _register_all_commands()
    model = AppModel()
    model.fullscreen = "palette"
    submitted: list = []
    model.palette_submit = lambda text: submitted.append(text) or True
    harness = Harness(100)
    element = h(CommandPalette, {"model": model, "width": 100})
    harness.render(element)
    router = harness.reconciler._input_router_cache[1]
    for ch in "theme":
        assert router(KeyEvent(kind="char", char=ch)) is True
        harness.render(element)
    out = harness.render(element)
    assert "/theme" in out
    assert router(KeyEvent(kind="enter")) is True
    assert submitted == ["/theme"]
    assert model.fullscreen == ""
    assert recent_commands(model)[0] == "theme"


def test_palette_handles_multiple_chars_in_same_frame():
    """同一输入批内的多个字符不丢失（handler 经 ref 推进查询）。"""
    from src.tui.app.command_palette import CommandPalette
    from src.tui.ink import h

    _register_all_commands()
    model = AppModel()
    model.fullscreen = "palette"
    harness = Harness(100)
    element = h(CommandPalette, {"model": model, "width": 100})
    harness.render(element)
    router = harness.reconciler._input_router_cache[1]
    assert router(KeyEvent(kind="char", char="t")) is True
    assert router(KeyEvent(kind="char", char="h")) is True
    out = harness.render(element)
    assert "th" in out
    assert "/theme" in out


def test_palette_tab_prefills_only():
    from src.tui.app.command_palette import CommandPalette
    from src.tui.ink import h

    _register_all_commands()
    model = AppModel()
    model.fullscreen = "palette"
    prefilled: list = []
    model.palette_prefill = lambda text: prefilled.append(text) or True
    harness = Harness(100)
    element = h(CommandPalette, {"model": model, "width": 100})
    harness.render(element)
    router = harness.reconciler._input_router_cache[1]
    for ch in "theme":
        router(KeyEvent(kind="char", char=ch))
        harness.render(element)
    assert router(KeyEvent(kind="tab")) is True
    assert prefilled == ["/theme "]
    assert model.fullscreen == ""


def test_palette_escape_and_f2_close():
    from src.tui.app.command_palette import CommandPalette
    from src.tui.ink import h

    for key in (KeyEvent(kind="escape"), KeyEvent(kind="f2")):
        model = AppModel()
        model.fullscreen = "palette"
        harness = Harness(100)
        harness.render(h(CommandPalette, {"model": model, "width": 100}))
        assert harness.reconciler._input_router_cache[1](key) is True
        assert model.fullscreen == ""


def test_f2_dispatches_palette_toggle():
    from src.tui._input_dispatcher import InputDispatcher

    dispatcher = InputDispatcher(
        io=SimpleNamespace(), buffer_editor=SimpleNamespace(), parser=SimpleNamespace()
    )
    hits: list = []
    dispatcher.set_palette_toggle_callback(lambda: hits.append(True))
    dispatcher._dispatch_key_event(KeyEvent(kind="f2"))
    assert hits == [True]


# ── 帮助视图内执行命令 ──────────────────────────────────

def test_help_enter_executes_selected_command():
    from src.tui.app.help_view import HelpView, _help_sections, _visible_sections
    from src.tui.ink import h

    _register_all_commands()
    sections = _help_sections(100)
    visible = _visible_sections(sections, frozenset(), "")
    target_idx = next(
        idx for idx, sec in enumerate(visible)
        if sec.get("kind") == "cmd" and sec.get("name") == "theme"
    )
    model = AppModel()
    model.fullscreen = "help"
    submitted: list = []
    model.palette_submit = lambda text: submitted.append(text) or True
    harness = Harness(100)
    element = h(HelpView, {"model": model, "width": 100})
    harness.render(element)
    router = harness.reconciler._input_router_cache[1]
    for _ in range(target_idx):
        assert router(KeyEvent(kind="arrow_down")) is True
        harness.render(element)
    assert router(KeyEvent(kind="enter")) is True
    assert submitted == ["/theme"]
    assert model.fullscreen == ""


def test_input_exposes_palette_toggle_callback(tmp_path):
    """Input 外观必须提供 F2 回调委托（装配回归防护）。"""
    from src.tui._input import Input

    fd = os.open(os.devnull, os.O_RDONLY)
    try:
        inp = Input(fd=fd, history_file=tmp_path / "history.txt")
        cb = lambda: None  # noqa: E731
        inp.set_palette_toggle_callback(cb)
        assert inp._dispatcher.get_callback("palette_toggle") is cb
    finally:
        os.close(fd)


# ── Input 提交 / 填入 ───────────────────────────────────

def test_input_submit_and_prefill_text(tmp_path):
    from src.tui._input import Input

    fd = os.open(os.devnull, os.O_RDONLY)
    try:
        inp = Input(fd=fd, history_file=tmp_path / "history.txt")
        echoes: list = []
        inp.set_echo_callback(lambda text, pos: echoes.append((text, pos)))

        assert inp.prefill_text("/theme ") is True
        assert inp.get_current_text() == "/theme "
        assert inp.get_queued_input() is None
        assert echoes[-1][0] == "/theme "

        assert inp.submit_text("/theme") is True
        assert inp.get_queued_input() == "/theme"
        assert inp.get_current_text() == ""
        assert inp.submit_text("") is False
    finally:
        os.close(fd)


# ── 命令动作辅助 ────────────────────────────────────────

def test_command_text_and_recent_helpers():
    model = AppModel()
    assert command_text("/theme") == "/theme"
    assert command_text("theme", with_args_space=True) == "/theme "
    assert run_command(model, "/theme") is False  # 未注入执行器
    remember_recent(model, "theme")
    remember_recent(model, "help")
    remember_recent(model, "theme")
    assert recent_commands(model)[:2] == ["theme", "help"]
    assert len(recent_commands(model)) <= 8


# ── 状态栏工作区段 ──────────────────────────────────────

def test_workspace_segment_contains_branch_and_directory():
    from src.tui.app.status_bar import StatusContext, _workspace_segment

    model = AppModel()
    ctx = StatusContext(
        model=model, status=model.status, status_active=False,
        dot_elapsed=0.0, spinner_char="\u00b7",
        reasoning_effort="", snapshot={},
    )
    text = _text(_workspace_segment(ctx))
    assert text
    assert "\u2387" in text or "~" in text or "/" in text
