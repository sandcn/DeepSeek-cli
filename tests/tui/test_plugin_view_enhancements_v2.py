"""plugin 第二批增强测试（2026-10-07 用户需求：更好的显示 / 操作 / 更多功能）。

覆盖：
  1. 组合过滤（搜索 ∩ 状态 ∩ 分类）：``_filter_allowed`` / 循环辅助；
  2. 依赖关系视图（``plugin_relation.relation_rows`` + ``r`` 键 + Enter 跳转）；
  3. 服务交叉引用（``_compute_dependents``）；
  4. 清单导出（``plugin_export``）。
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


def _text_of(el) -> str:
    parts: list[str] = []
    stack = [el]
    while stack:
        node = stack.pop()
        props = getattr(node, "props", None) or {}
        styled = props.get("styled")
        if styled:
            parts.append("".join(getattr(r, "text", "") for r in styled))
        else:
            parts.append(str(props.get("children", "")))
        stack.extend(getattr(node, "children", None) or [])
    return "\n".join(parts)


def _make(name, state="ACTIVE", depends=None, provides=None, kind="kernel"):
    from src.plugins.view_model import _make_entry
    return _make_entry(
        name, kind, state, [("状态", state), ("错误", "(无)")],
        depends=depends, provides=provides,
    )


def _entries():
    a = _make("a", "ACTIVE", depends=["b"], provides=["svc-a"])
    b = _make("b", "ACTIVE", depends=[], provides=["svc-b"])
    c = _make("c", "FAILED", depends=["a"])
    return [a, b, c]


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


# ═══════════════════════════════════════════════════════════
# 1. 组合过滤
# ═══════════════════════════════════════════════════════════


class TestFilters:

    def test_filter_allowed_none(self):
        from src.tui.app.plugin_view import _filter_allowed
        entries = _entries()
        assert _filter_allowed(entries, "", [], False, "", "") is None

    def test_filter_allowed_state(self):
        from src.tui.app.plugin_view import _filter_allowed
        entries = _entries()
        allowed = _filter_allowed(entries, "", [], False, "FAILED", "")
        assert allowed == {2}

    def test_filter_allowed_search_and_state(self):
        from src.tui.app.plugin_view import _filter_allowed
        entries = _entries()
        # 搜索匹配 all（[{0,1,2}]）∩ 状态 ACTIVE → {0,1}
        allowed = _filter_allowed(entries, "a", [0, 1, 2], True, "ACTIVE", "")
        assert allowed == {0, 1}

    def test_state_options(self):
        from src.tui.app.plugin_view import _state_options
        assert _state_options(_entries()) == ["", "ACTIVE", "FAILED"]

    def test_kind_options(self):
        from src.tui.app.plugin_view import _kind_options
        assert _kind_options(_entries()) == ["", "kernel"]

    def test_cycle_state_filter(self):
        from src.tui.app._state_types import PluginViewState
        from src.tui.app.plugin_view import _cycle_state_filter
        pv = PluginViewState()
        entries = _entries()
        assert _cycle_state_filter(pv, entries) == "ACTIVE"
        assert _cycle_state_filter(pv, entries) == "FAILED"
        assert _cycle_state_filter(pv, entries) == ""

    def test_cycle_kind_filter(self):
        from src.tui.app._state_types import PluginViewState
        from src.tui.app.plugin_view import _cycle_kind_filter
        pv = PluginViewState()
        assert _cycle_kind_filter(pv, _entries()) == "kernel"
        assert _cycle_kind_filter(pv, _entries()) == ""

    def test_S_key_cycles_and_renders(self):
        from src.tui.app.plugin_view import PluginView
        model = _model()
        fiber, el = _render_component(PluginView, model)
        handler = _find_input_handler(fiber)
        assert handler(_ev("char", "S")) is True
        assert model.plugin_view.filter_state == "ACTIVE"
        _fiber2, el2 = _render_component(PluginView, model)
        assert "状态 ACTIVE" in _text_of(el2)

    def test_K_key_cycles(self):
        from src.tui.app.plugin_view import PluginView
        model = _model()
        fiber, _el = _render_component(PluginView, model)
        handler = _find_input_handler(fiber)
        assert handler(_ev("char", "K")) is True
        assert model.plugin_view.filter_kind == "kernel"


# ═══════════════════════════════════════════════════════════
# 2. 依赖关系视图
# ═══════════════════════════════════════════════════════════


class TestRelation:

    def test_compute_dependents(self):
        from src.plugins.view_model import _compute_dependents
        entries = _entries()
        _compute_dependents(entries)
        assert entries[1]["dependents"] == ["a"]  # b 被 a 依赖
        assert entries[0]["dependents"] == ["c"]  # a 被 c 依赖

    def test_relation_rows_targets(self):
        from src.tui.app.plugin_relation import relation_rows
        entries = _entries()
        by_name = {e["name"]: e for e in entries}
        rows, targets = relation_rows(entries[0], by_name, 40)
        assert rows and len(rows) == len(targets)
        assert "b" in targets  # a 的依赖 b 可跳转
        text = "\n".join("".join(r.text for r in row) for row in rows)
        assert "a 关系" in text
        assert "依赖" in text

    def test_relation_rows_empty(self):
        from src.tui.app.plugin_relation import relation_rows
        rows, targets = relation_rows(None, {}, 40)
        assert rows and targets == [None]

    def test_relation_rows_unloaded(self):
        from src.tui.app.plugin_relation import relation_rows
        entry = _make("x", depends=["missing-svc"])
        rows, targets = relation_rows(entry, {"x": entry}, 40)
        assert None in targets  # 未加载 → 不可跳转
        text = "\n".join("".join(r.text for r in row) for row in rows)
        assert "(未加载)" in text

    def test_r_key_toggles(self):
        from src.tui.app.plugin_view import PluginView
        model = _model()
        fiber, _el = _render_component(PluginView, model)
        handler = _find_input_handler(fiber)
        assert handler(_ev("char", "r")) is True
        assert model.plugin_view.relation_open is True
        assert handler(_ev("char", "r")) is True
        assert model.plugin_view.relation_open is False

    def test_relation_rendered(self):
        from src.tui.app.plugin_view import PluginView
        model = _model(relation_open=True, pane="detail")
        _fiber, el = _render_component(PluginView, model)
        text = _text_of(el)
        assert "关系" in text
        assert "被依赖" in text

    def test_relation_enter_jumps(self):
        from src.tui.app.plugin_view import PluginView
        from src.tui.app.plugin_relation import relation_rows
        entries = _entries()
        model = _model(entries, relation_open=True, pane="detail", selected=1)
        fiber, _el = _render_component(PluginView, model)
        handler = _find_input_handler(fiber)
        by_name = {e["name"]: e for e in entries}
        _rows, targets = relation_rows(entries[0], by_name, 60)
        model.plugin_view.cursor = targets.index("b")
        assert handler(_ev("enter")) is True
        assert model.plugin_view.relation_open is False
        # b 显示行 = 2（分类分隔行 0 + a 行 1 + b 行 2）
        assert model.plugin_view.selected == 2

    def test_relation_escape_closes_panel(self):
        from src.tui.app.plugin_view import PluginView
        model = _model(relation_open=True, pane="detail")
        fiber, _el = _render_component(PluginView, model)
        handler = _find_input_handler(fiber)
        assert handler(_ev("escape")) is True
        assert model.plugin_view.relation_open is False
        assert model.plugin_view.done is False  # 视图未关闭

    def test_relation_h_closes_panel(self):
        from src.tui.app.plugin_view import PluginView
        model = _model(relation_open=True, pane="detail")
        fiber, _el = _render_component(PluginView, model)
        handler = _find_input_handler(fiber)
        assert handler(_ev("char", "h")) is True
        assert model.plugin_view.relation_open is False


# ═══════════════════════════════════════════════════════════
# 3. 清单导出
# ═══════════════════════════════════════════════════════════


class TestExport:

    def test_markdown(self):
        from src.tui.app.plugin_export import entries_to_markdown
        text = entries_to_markdown(_entries())
        assert "# 插件清单" in text
        assert "## a" in text
        assert "svc-a" in text

    def test_json(self):
        import json
        from src.tui.app.plugin_export import entries_to_json
        payload = json.loads(entries_to_json(_entries()))
        assert payload["meta"]["total"] == 3
        assert payload["entries"][0]["name"] == "a"
        assert payload["entries"][0]["provides"] == ["svc-a"]

    def test_filename(self):
        from src.tui.app.plugin_export import export_filename
        name = export_filename("json", when=0)
        assert name.startswith("plugin-export-") and name.endswith(".json")

    def test_write_export(self, tmp_path):
        from src.tui.app.plugin_export import write_export
        path = write_export(_entries(), "md", directory=str(tmp_path))
        assert path.endswith(".md")
        import os
        assert os.path.exists(os.path.join(str(tmp_path), os.path.basename(path)))

    def test_unknown_format(self):
        import pytest as _pytest
        from src.tui.app.plugin_export import write_export
        with _pytest.raises(ValueError):
            write_export(_entries(), "xml")

    def test_w_key_exports(self, monkeypatch, tmp_path):
        from src.tui.app import plugin_view as pv_mod
        from src.tui.app.plugin_view import PluginView
        monkeypatch.chdir(tmp_path)
        model = _model()
        fiber, _el = _render_component(PluginView, model)
        handler = _find_input_handler(fiber)
        assert handler(_ev("char", "w")) is True
        assert "导出" in model.plugin_view.status_message

    def test_do_export_empty(self):
        from src.tui.app._state_types import PluginViewState
        from src.tui.app.plugin_view import _do_export
        pv = PluginViewState()
        _do_export(pv, [], "md")
        assert "无" in pv.status_message

    def test_W_key_exports_json(self, monkeypatch, tmp_path):
        from src.tui.app.plugin_view import PluginView
        monkeypatch.chdir(tmp_path)
        model = _model()
        fiber, _el = _render_component(PluginView, model)
        handler = _find_input_handler(fiber)
        assert handler(_ev("char", "W")) is True
        assert "JSON" in model.plugin_view.status_message
