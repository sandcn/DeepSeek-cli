"""plugin 增强测试（2026-10-07 用户需求：更好的显示 / 操作 / 更多功能）。

覆盖：
  1. 详情字段渲染增强（错误 / 缺失依赖 / 异常状态警示级别 + 左栏警示标记）；
  2. 插件统计概览（总数 / 按状态 / 错误 / 缺失依赖计数）；
  3. 搜索过滤（``/`` + ``n``/``N`` + ``f``）；
  4. 帮助面板（``?``）；
  5. 复制插件信息（``y``，OSC52）。
"""

from __future__ import annotations

from types import SimpleNamespace

from src.tui.ink.fiber import InputHook
from src.tui.ink import hooks
from src.tui.ink.fiber import TAG_FUNCTION, Fiber

# ═══════════════════════════════════════════════════════════
# 通用辅助
# ═══════════════════════════════════════════════════════════


def _render_component(component, model, width=100):
    fiber = Fiber(TAG_FUNCTION, component, {"model": model, "width": width})
    hooks._push_current(fiber)
    try:
        el = component({"model": model, "width": width})
    finally:
        hooks._pop_current()
    return fiber, el


def _find_input_handler(fiber):
    for hook in getattr(fiber, "hooks", None) or []:
        if isinstance(hook, InputHook) and hook.is_active and hook.handler is not None:
            return hook.handler
    return None


def _ev(kind: str, char: str = ""):
    return SimpleNamespace(
        kind=kind, char=char, modifier=0, keycode=0, raw=b"",
        kitty_bits=-1, event_type="",
    )


def _entries():
    from src.plugins.view_model import _make_entry

    ok = _make_entry("tools", "kernel", "ACTIVE", [
        ("\u72b6\u6001", "ACTIVE"),
        ("\u6765\u6e90", "src.plugins.tools"),
        ("\u7f3a\u5931\u4f9d\u8d56", "(\u65e0)"),
        ("\u9519\u8bef", "(\u65e0)"),
    ])
    broken = _make_entry("broken", "kernel", "FAILED", [
        ("\u72b6\u6001", "FAILED"),
        ("\u6765\u6e90", "src.plugins.broken"),
        ("\u7f3a\u5931\u4f9d\u8d56", "config"),
        ("\u9519\u8bef", "ValueError('boom')"),
    ])
    pending = _make_entry("pending", "kernel", "PENDING", [
        ("\u72b6\u6001", "PENDING"),
        ("\u6765\u6e90", "src.plugins.pending"),
    ])
    return [ok, broken, pending]


def _model(entries=None, **kw):
    from src.tui.app.model import AppModel, PluginViewState

    model = AppModel()
    model.plugin_view = PluginViewState(
        visible=True, seq=1,
        entries=entries if entries is not None else _entries(),
        **kw,
    )
    model.fullscreen = "plugin"
    return model


def _text_of(el) -> str:
    parts: list[str] = []
    stack = [el]
    while stack:
        node = stack.pop()
        props = getattr(node, "props", None) or {}
        styled = props.get("styled")
        if styled:
            parts.append("".join(getattr(r, "text", "") or "" for r in styled))
        ch = props.get("children")
        if isinstance(ch, str):
            parts.append(ch)
        for child in getattr(node, "children", None) or []:
            stack.append(child)
    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════
# 1. view_model 增强
# ═══════════════════════════════════════════════════════════


class TestViewModelEnhancements:

    def test_field_levels(self):
        from src.plugins.view_model import _field_levels
        levels = _field_levels([
            ("\u72b6\u6001", "FAILED"),
            ("\u7f3a\u5931\u4f9d\u8d56", "config"),
            ("\u9519\u8bef", "boom"),
            ("\u6765\u6e90", "x"),
            ("\u5b50\u63d2\u4ef6", "(\u65e0)"),
        ])
        assert levels["\u72b6\u6001"] == "error"
        assert levels["\u7f3a\u5931\u4f9d\u8d56"] == "warn"
        assert levels["\u9519\u8bef"] == "error"
        assert "\u6765\u6e90" not in levels
        assert "\u5b50\u63d2\u4ef6" not in levels

    def test_pending_state_warn(self):
        from src.plugins.view_model import _field_levels
        assert _field_levels([("\u72b6\u6001", "PENDING")])["\u72b6\u6001"] == "warn"

    def test_entry_alerts(self):
        from src.plugins.view_model import _make_entry
        entry = _make_entry("x", "kernel", "FAILED", [
            ("\u7f3a\u5931\u4f9d\u8d56", "config"), ("\u9519\u8bef", "boom"),
        ])
        assert set(entry["alerts"]) == {"\u9519\u8bef", "\u7f3a\u4f9d\u8d56"}
        ok = _make_entry("y", "kernel", "ACTIVE", [("\u9519\u8bef", "(\u65e0)")])
        assert ok["alerts"] == []

    def test_collect_and_format_stats(self):
        from src.plugins.view_model import collect_plugin_stats, format_plugin_stats
        stats = collect_plugin_stats(_entries())
        assert stats["total"] == 3
        assert stats["by_state"]["ACTIVE"] == 1
        assert stats["errors"] == 1
        assert stats["missing"] == 1
        assert stats["warnings"] == 1
        text = format_plugin_stats(stats)
        assert "3" in text and "ACTIVE" in text
        assert "\u9519\u8bef 1" in text and "\u7f3a\u4f9d\u8d56 1" in text

    def test_plugin_search_text(self):
        from src.plugins.view_model import plugin_search_text
        entries = _entries()
        assert "tools" in plugin_search_text(entries[0])
        assert "src.plugins.tools" in plugin_search_text(entries[0])

    def test_format_plugin_entry_text(self):
        from src.plugins.view_model import format_plugin_entry_text
        text = format_plugin_entry_text(_entries()[0])
        assert text.startswith("tools")
        assert "\u72b6\u6001: ACTIVE" in text

    def test_collect_stats_empty(self):
        from src.plugins.view_model import collect_plugin_stats, format_plugin_stats
        stats = collect_plugin_stats([])
        assert stats["total"] == 0
        assert "0" in format_plugin_stats(stats)


# ═══════════════════════════════════════════════════════════
# 2. 组件：渲染增强
# ═══════════════════════════════════════════════════════════


class TestPluginViewRender:

    def _render(self, model, width=100):
        from src.tui.app.plugin_view import PluginView
        return _render_component(PluginView, model, width=width)

    def test_header_shows_stats(self):
        _f, el = self._render(_model())
        text = _text_of(el)
        assert "\u5df2\u52a0\u8f7d\u63d2\u4ef6" in text
        assert "3" in text

    def test_alert_marker_in_list(self):
        from src.tui.ink.widgets.listview import ListView
        _f, el = self._render(_model())
        lv = None
        stack = [el]
        while stack:
            node = stack.pop()
            if getattr(node, "type", None) == ListView:
                lv = node
                break
            for child in getattr(node, "children", None) or []:
                stack.append(child)
        assert lv is not None
        render_item = lv.props["renderItem"]
        items = lv.props["items"]
        broken_idx = items.index(_entries()[1])
        row = render_item(items[broken_idx], broken_idx, False)
        row_text = "".join(r.text for r in row.props["styled"])
        assert "\u26a0" in row_text

    def test_detail_shows_error_field(self):
        model = _model()
        # display_items = [分类分隔(None), tools, broken, pending] → 索引 2
        model.plugin_view.selected = 2
        _f, el = self._render(model)
        text = _text_of(el)
        assert "ValueError" in text

    def test_help_panel(self):
        model = _model(help_open=True)
        _f, el = self._render(model)
        text = _text_of(el)
        assert "\u5e2e\u52a9" in text
        assert "Enter" in text

    def test_status_line(self):
        model = _model(search_pattern="tools", search_matches=[0])
        _f, el = self._render(model)
        assert "/tools" in _text_of(el)


# ═══════════════════════════════════════════════════════════
# 3. 组件：事件键位
# ═══════════════════════════════════════════════════════════


class TestPluginViewEvents:

    def _handler(self, model, width=100):
        from src.tui.app.plugin_view import PluginView
        fiber, _el = _render_component(PluginView, model, width=width)
        return _find_input_handler(fiber)

    def test_slash_search_and_execute(self):
        model = _model()
        handler = self._handler(model)
        pv = model.plugin_view
        assert handler(_ev("char", "/")) is True
        assert pv.search_mode is True
        handler(_ev("char", "t"))
        handler(_ev("char", "o"))
        assert pv.search_query == "to"
        assert handler(_ev("enter")) is True
        assert pv.search_mode is False
        assert pv.search_pattern == "to"
        assert pv.search_matches == [0]

    def test_search_escape_clears(self):
        model = _model()
        handler = self._handler(model)
        pv = model.plugin_view
        handler(_ev("char", "/"))
        handler(_ev("char", "x"))
        assert handler(_ev("escape")) is True
        assert pv.search_mode is False
        assert pv.search_query == ""

    def test_next_match(self):
        model = _model()
        pv = model.plugin_view
        pv.search_pattern = "kernel"
        pv.search_matches = [0, 1, 2]
        pv.search_idx = 0
        handler = self._handler(model)
        assert handler(_ev("char", "n")) is True
        assert pv.search_idx == 1
        assert handler(_ev("char", "N")) is True
        assert pv.search_idx == 0

    def test_filter_key(self):
        model = _model()
        pv = model.plugin_view
        pv.search_pattern = "tools"
        pv.search_matches = [0]
        handler = self._handler(model)
        assert handler(_ev("char", "f")) is True
        assert pv.search_filter is True

    def test_help_toggle(self):
        model = _model()
        handler = self._handler(model)
        pv = model.plugin_view
        assert handler(_ev("char", "?")) is True
        assert pv.help_open is True
        assert pv.pane == "detail"  # 帮助打开 → 焦点右栏（可滚动）
        assert handler(_ev("char", "?")) is True
        assert pv.help_open is False
        assert pv.pane == "list"

    def test_copy_key(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            "src.tui._screen.set_clipboard",
            lambda text: captured.setdefault("text", text) or True,
        )
        model = _model()
        handler = self._handler(model)
        assert handler(_ev("char", "y")) is True
        assert "tools" in captured["text"]

    def test_search_mode_swallows_navigation(self):
        model = _model()
        handler = self._handler(model)
        pv = model.plugin_view
        handler(_ev("char", "/"))
        selected_before = pv.selected
        handler(_ev("char", "j"))  # 进入搜索词的字符，不导航
        assert pv.search_query == "j"
        handler(_ev("arrow_down"))
        assert pv.selected == selected_before
