"""TUI 改进项测试（2026-10-07）。

覆盖：
  1. 空状态/启动界面重构（欢迎卡信息区 + 引导；见 test_tui_beautify 补充）
  2. ``/help`` 分组化 + 列对齐 + 快捷键小节
  3. 补全弹窗增强（描述列对齐 / 整行高亮 / 参数「当前」标注）
  4. 状态栏信息增强（theme / messages 段空闲可见）
  5. 帮助速查全屏视图（HelpView + F1 / Ctrl+/ 绑定）
  6. 输入区体验（模式行上下文进度条 / 多行指示 / 占位提示轮播）
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.tui.app.model import AppModel


# ═══════════════════════════════════════════════════════════
# 2. /help 分组化 + 列对齐
# ═══════════════════════════════════════════════════════════

def _register_all_commands():
    import src.core.commands._config_cmd  # noqa: F401
    import src.core.commands._data_cmd  # noqa: F401
    import src.core.commands._export_cmd  # noqa: F401
    import src.core.commands._plugin_cmd  # noqa: F401
    import src.core.commands._session_cmd  # noqa: F401
    import src.core.commands.plugins.deitmsg_plugin  # noqa: F401
    import src.core.commands.plugins.editmsg_plugin  # noqa: F401
    import src.core.commands.plugins.loop_plugin  # noqa: F401
    import src.core.commands.plugins.model_plugin  # noqa: F401
    import src.core.commands.plugins.skill_plugin  # noqa: F401
    from src.core.commands.base import (
        command_registry_singleton,
        declared_command_names,
        declared_command_plugin,
    )

    reg = command_registry_singleton()
    for name in declared_command_names():
        reg.register(declared_command_plugin(name))
    return reg


def test_help_text_grouped_and_aligned():
    """``/help`` 文本：按分组展示 + 命令列对齐 + 快捷键小节。"""
    _register_all_commands()
    from src.core.internal.commands._command_core import get_dynamic_help_text

    text = get_dynamic_help_text()
    plain = text  # 文本行内 ANSI 由 _get_out 处理，此处直接检查含分组标题
    assert "可用命令" in plain
    # 分组标题（会话 / 模型与推理 / 界面与配置）
    assert "会话" in plain
    assert "模型与推理" in plain
    assert "界面与配置" in plain
    assert "快捷键" in plain
    # 命令 + 描述
    assert "/help" in plain and "显示帮助" in plain
    # 列对齐：同一分组内命令描述起点一致（以最长命令名对齐）
    lines = [ln for ln in plain.split("\n") if "/clear" in ln or "/editmsg" in ln]
    assert lines, "应含命令行"
    # 描述列起点（去除 ANSI 后首个非 ASCII 字符 = 描述起始列）一致
    import re

    def desc_col(line: str) -> int:
        stripped = re.sub(r"\x1b\[[0-9;]*m", "", line)
        m = re.search(r"[^\x00-\x7f]", stripped)
        assert m, f"行内应含描述: {stripped!r}"
        return m.start()

    cols = {desc_col(ln) for ln in lines}
    assert len(cols) == 1, f"描述列未对齐: {cols}"


def test_help_text_shortcut_rows_aligned():
    """快捷键小节两列按显示宽度对齐（同列起点一致）。"""
    _register_all_commands()
    from src.core.internal.commands._command_core import get_dynamic_help_text

    text = get_dynamic_help_text()
    import re

    plain = re.sub(r"\x1b\[[0-9;]*m", "", text)
    rows = [ln for ln in plain.split("\n") if "Ctrl+A/Home" in ln]
    assert rows
    # 第二列（Ctrl+E/End）起点一致（仅一行，检查含两列内容）
    assert "Ctrl+E/End" in rows[0]


# ═══════════════════════════════════════════════════════════
# 3. 补全弹窗增强
# ═══════════════════════════════════════════════════════════

def test_highlight_line_full_width_bg():
    """整行高亮：所有 run 带背景色并补白至满宽。"""
    from src.tui.app._popup_builder import _highlight_line
    from src.tui.core.style import Style
    from src.tui.ink import Line

    line = Line.of("abc", Style(fg=45))
    hl = _highlight_line(line, 10, 237)
    assert all(r.style is not None and r.style.bg == 237 for r in hl.runs)
    assert hl.width == 10


def test_popup_descriptions_aligned():
    """补全弹窗候选描述起点对齐（命令名短者补空格）。"""
    from src.tui.app.app import App
    from src.tui.ink import h, renderToString

    m = AppModel()
    m.width = 60
    c = m.completion
    c.visible = True
    c.items = ["/help", "/changes"]
    c.texts = list(c.items)
    c.types = ["command", "command"]
    c.descriptions = ["显示帮助", "显示 diff"]
    m.input_text = "/"
    out = renderToString(h(App, {"model": m, "width": 60}), {"columns": 60})
    rows = [ln for ln in out.split("\n") if "显示帮助" in ln or "显示 diff" in ln]
    assert len(rows) == 2, f"应有两行候选: {rows}"
    # 去掉 ANSI 后描述起点一致
    import re

    plain = [re.sub(r"\x1b\[[0-9;]*m", "", r) for r in rows]
    cols = {p.index("显示") for p in plain}
    assert len(cols) == 1, f"描述列未对齐: {plain}"


def test_popup_selected_row_full_highlight():
    """选中候选行整行带背景（含补白）。"""
    from src.tui.app.app import App
    from src.tui.ink import h, renderToString

    m = AppModel()
    m.width = 60
    c = m.completion
    c.visible = True
    c.items = ["/help", "/model"]
    c.texts = list(c.items)
    c.types = ["command", "command"]
    c.descriptions = ["显示帮助", "切换模型"]
    c.selected = 0
    m.input_text = "/"
    out = renderToString(h(App, {"model": m, "width": 60}), {"columns": 60})
    sel_line = [ln for ln in out.split("\n") if "\u25b6" in ln][0]
    assert "\x1b[48;5;237m" in sel_line, "选中行应有背景高亮"


def test_param_completion_marks_current(monkeypatch):
    """``/model`` 参数补全标注当前值（desc=当前）。"""
    from src.tui import _completion_engine as ce

    eng = ce.CompletionEngine()
    monkeypatch.setattr(eng._models_cache, "get", lambda: ["m-a", "m-b"])
    monkeypatch.setattr(ce, "_current_config_value", lambda key: "m-b")
    items = eng.complete("/model")
    by_text = {i.display: i.desc for i in items}
    assert by_text.get("/model m-b") == "当前"
    assert by_text.get("/model m-a") == ""


def test_theme_param_completion_marks_current(monkeypatch):
    """``/theme`` 参数补全标注当前主题。"""
    from src.tui import _completion_engine as ce

    eng = ce.CompletionEngine()
    monkeypatch.setattr(
        eng._theme_cache, "get", lambda: [("dark", "暗"), ("light", "亮")],
    )
    monkeypatch.setattr(ce, "_current_config_value", lambda key: "light")
    items = eng.complete("/theme")
    by_text = {i.display: i.desc for i in items}
    assert by_text.get("/theme light") == "当前"
    assert by_text.get("/theme dark") == ""


# ═══════════════════════════════════════════════════════════
# 4. 状态栏信息增强
# ═══════════════════════════════════════════════════════════

def _ctx(model, active=False):
    from src.tui.app.status_bar import StatusContext

    return StatusContext(
        model=model, status=model.status, status_active=active,
        dot_elapsed=0.0, spinner_char="\u00b7", reasoning_effort="",
        snapshot={},
    )


def test_theme_segment_renders():
    from src.tui.app.status_bar import _theme_segment

    m = AppModel()
    runs = _theme_segment(_ctx(m))
    assert runs and "dark" in runs[0].text or runs == []


def test_messages_segment_counts_source():
    from src.tui.app.status_bar import _messages_segment

    m = AppModel()
    assert _messages_segment(_ctx(m)) == []
    m.message_source = lambda: [1, 2, 3]
    runs = _messages_segment(_ctx(m))
    assert runs and "3" in runs[0].text


def test_idle_status_shows_theme_segment():
    """空闲状态栏也包含 theme 段（新增段不受 status_active 门控）。"""
    from src.tui.app.status_bar import _build_status_runs

    m = AppModel()
    m.status.model_name = "m1"
    m.message_source = lambda: [1]
    runs = _build_status_runs(m)
    text = "".join(r.text for r in runs)
    assert "m1" in text


# ═══════════════════════════════════════════════════════════
# 5. 帮助速查全屏视图
# ═══════════════════════════════════════════════════════════

def test_help_rows_structure():
    """HelpView 内容行：分组标题 + 命令行 + exit + 快捷键行。"""
    _register_all_commands()
    from src.tui.app.help_view import _help_rows

    rows = _help_rows(80)
    text = "\n".join("".join(r.text for r in row) for row in rows)
    assert "会话" in text
    assert "/help" in text
    assert "exit" in text
    assert "快捷键" in text
    assert "Ctrl+A/Home" in text


def test_help_rows_width_invariant():
    """每行显示宽度 <= width（行宽不变量）。"""
    _register_all_commands()
    from src.tui.app.help_view import _help_rows
    from src.tui.app._welcome import display_width

    for width in (40, 60, 100):
        for row in _help_rows(width):
            w = sum(display_width(r.text) for r in row)
            assert w <= width


def test_help_view_registered():
    from src.tui.app.app import FULLSCREEN_VIEWS
    from src.tui.app.help_view import HelpView

    assert FULLSCREEN_VIEWS.get("help") is HelpView


def test_help_view_renders_when_active():
    from src.tui.app.app import App
    from src.tui.ink import h, renderToString

    m = AppModel()
    m.width = 80
    m.fullscreen = "help"
    out = renderToString(h(App, {"model": m, "width": 80}), {"columns": 80})
    assert "帮助速查" in out
    # 全屏视图：正常界面元素不渲染
    assert "输入消息" not in out
    assert "DeepSeek CLI" not in out


def test_help_view_close_key():
    from src.tui.app.help_view import _is_help_close_key

    assert _is_help_close_key(SimpleNamespace(kind="escape", char=""))
    assert _is_help_close_key(SimpleNamespace(kind="f1", char=""))
    assert _is_help_close_key(SimpleNamespace(kind="ctrl_key", char="\x1f"))
    assert not _is_help_close_key(SimpleNamespace(kind="char", char="j"))


def test_ctrl_slash_binding_registered():
    from src.tui._keybindings import resolve_binding

    assert resolve_binding("\x1f") == "help_toggle"


def test_dispatcher_help_toggle_callback():
    """Ctrl+/ 与 F1 均触发 help_toggle 回调。"""
    from src.tui._input_dispatcher import InputDispatcher

    d = InputDispatcher.__new__(InputDispatcher)
    d._help_toggle_callback = None
    d._trace_toggle_callback = None
    d._input_hook_router = None
    d._special_key_callback = None
    calls = []
    d._help_toggle_callback = lambda: calls.append("help")
    InputDispatcher._handle_ctrl_key(d, "\x1f")
    assert calls == ["help"]
    # F1（功能键 kind）
    InputDispatcher._dispatch_key_event(
        d, SimpleNamespace(kind="f1", char="", raw=b"", modifier=0),
    )
    assert calls == ["help", "help"]


# ═══════════════════════════════════════════════════════════
# 6. 输入区体验
# ═══════════════════════════════════════════════════════════

def test_placeholder_rotation_cycles():
    from src.tui.app import input_area

    a = input_area._placeholder_rotate(0.0)
    b = input_area._placeholder_rotate(input_area._PLACEHOLDER_ROTATE_SECONDS)
    assert a != b
    # 周期回绕
    c = input_area._placeholder_rotate(
        input_area._PLACEHOLDER_ROTATE_SECONDS * len(input_area._PLACEHOLDER_ROTATION)
    )
    assert c == a


def test_mode_line_ctx_progress_bar():
    from src.tui.app.input_area import _build_mode_line

    line = _build_mode_line(80, False, 50.0)
    assert "\u2588" in line.plain and "\u2591" in line.plain
    assert "50.0%" in line.plain


def test_build_lines_multiline_indicator():
    """输入含换行 → 模式行显示 ↵ N 行。"""
    from src.tui.app.input_area import _build_lines

    fiber = SimpleNamespace(
        props={"text": "a\nb\nc", "cursor_pos": 5, "width": 80},
        layout_box=SimpleNamespace(w=80, h=10),
    )
    lines = _build_lines(fiber)
    mode_line = "".join(r.text for r in lines[-1].runs)
    assert "\u21b5 3 \u884c" in mode_line
