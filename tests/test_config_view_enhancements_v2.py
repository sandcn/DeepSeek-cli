"""config 第二批增强测试（2026-10-07 用户需求：更好的显示 / 操作 / 更多功能）。

覆盖：
  1. 分组折叠与组间跳转（``_group_of`` / ``_config_rows`` / ``z*`` / ``[`` ``]``）；
  2. 编辑 diff 预览（``_diff_runs`` + 渲染）；
  3. 撤销历史面板（``_undo_entries`` / ``_undo_to`` / ``U``）；
  4. 配置导出 / 导入（``config_export`` + ``e`` / ``i``）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.tui.app._state_types import ConfigViewState

# ═══════════════════════════════════════════════════════════
# 通用辅助
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def isolated_rc(monkeypatch, tmp_path):
    from src.config import loader as cfg_loader
    from src.config import defaults as cfg_defaults

    rc_file = tmp_path / "chatrc.json"
    monkeypatch.setattr(cfg_loader, "RC_FILE", rc_file)
    monkeypatch.setattr(cfg_loader, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfg_loader, "LOG_FILE", tmp_path / "audit.log")
    monkeypatch.setattr(cfg_loader, "_RC_LOADED", False)
    monkeypatch.setattr(cfg_loader, "_RC", None)
    monkeypatch.setattr(cfg_defaults, "RC_FILE", rc_file)
    monkeypatch.setattr(cfg_defaults, "CONFIG_DIR", tmp_path)
    return rc_file


def _ev(kind: str, char: str = ""):
    return SimpleNamespace(
        kind=kind, char=char, modifier=0, keycode=0, raw=b"",
        kitty_bits=-1, event_type="",
    )


def _entry(key, path, value="v"):
    return {
        "key": key, "path": path, "type": str, "value": value,
        "value_text": str(value), "default_text": "", "desc": "",
        "sensitive": False, "options": None, "edit_kind": "input",
    }


def _entries():
    return [
        _entry("A", "performance.a"),
        _entry("B", "performance.b"),
        _entry("C", "model"),
    ]


def _state(entries=None, **kw):
    return ConfigViewState(
        visible=True, seq=1, entries=entries if entries is not None else _entries(),
        **kw,
    )


def _handle(cv, event, **kw):
    from src.tui.app.config_view import _handle_config_event
    entries = cv.entries
    return _handle_config_event(
        cv, entries, event, visible=True, total=len(entries),
        all_entries=entries, view_map=None, pane_vh=10,
    )


def _render(model, width=100):
    from src.tui.app.config_view import ConfigView
    from src.tui.ink import hooks
    from src.tui.ink.fiber import TAG_FUNCTION, Fiber

    fiber = Fiber(TAG_FUNCTION, ConfigView, {"model": model, "width": width})
    hooks._push_current(fiber)
    try:
        el = ConfigView({"model": model, "width": width})
    finally:
        hooks._pop_current()
    return fiber, el


def _model(**kw):
    from src.tui.app.model import AppModel
    model = AppModel()
    model.config_view = _state(**kw)
    model.fullscreen = "config"
    return model


def _text(el) -> str:
    parts: list = []
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


# ═══════════════════════════════════════════════════════════
# 1. 分组折叠与组间跳转
# ═══════════════════════════════════════════════════════════


class TestGroups:

    def test_group_of(self):
        from src.tui.app.config_view import _group_of, _TOP_GROUP
        assert _group_of(_entry("A", "performance.a")) == "performance"
        assert _group_of(_entry("B", "model")) == "model"
        # path 为空 → 回退 key
        assert _group_of(_entry("C", "")) == "C"
        assert _group_of({}) == _TOP_GROUP

    def test_config_rows_basic(self):
        from src.tui.app.config_view import _config_rows, _ConfigGroupRow
        entries = _entries()
        items, row_to_entry, entry_to_row = _config_rows(entries, set())
        assert isinstance(items[0], _ConfigGroupRow)
        assert items[0].group == "performance" and items[0].count == 2
        assert row_to_entry[0] == -1
        assert entry_to_row[0] == 1

    def test_config_rows_collapsed(self):
        from src.tui.app.config_view import _config_rows, _ConfigGroupRow
        entries = _entries()
        items, _r2e, entry_to_row = _config_rows(entries, {"performance"})
        assert isinstance(items[0], _ConfigGroupRow)
        assert items[0].collapsed is True
        # performance 组内条目隐藏 → 只剩 model 组头 + C
        assert 0 not in entry_to_row and 1 not in entry_to_row
        assert entry_to_row[2] == 2

    def test_is_config_selectable(self):
        from src.tui.app.config_view import _is_config_selectable, _ConfigGroupRow
        assert _is_config_selectable(_entry("A", "x")) is True
        assert _is_config_selectable(_ConfigGroupRow("g")) is False

    def test_toggle_group_collapse(self):
        from src.tui.app.config_view import _toggle_group_collapse
        cv = _state()
        entries = _entries()
        _toggle_group_collapse(cv, entries, 0, "toggle")
        assert cv.collapsed_groups == {"performance"}
        assert cv.selected == 2  # 折叠后选中移到 model（C）
        _toggle_group_collapse(cv, entries, 0, "toggle")
        assert cv.collapsed_groups == set()

    def test_collapse_all_groups(self):
        from src.tui.app.config_view import _collapse_all_groups
        cv = _state()
        _collapse_all_groups(cv, _entries(), True)
        assert cv.collapsed_groups == {"performance", "model"}
        _collapse_all_groups(cv, _entries(), False)
        assert cv.collapsed_groups == set()

    def test_jump_group(self):
        from src.tui.app.config_view import _jump_group
        cv = _state()
        entries = _entries()
        _jump_group(cv, entries, 0, 1)
        assert cv.selected == 2  # 跳到 model 分组首条
        _jump_group(cv, entries, 2, -1)
        assert cv.selected == 0  # 回到 performance 分组首条

    def test_z_prefix_keys(self):
        cv = _state()
        assert _handle(cv, _ev("char", "z")) is True
        assert cv.pending_prefix == "z"
        assert _handle(cv, _ev("char", "c")) is True
        assert cv.pending_prefix == ""
        assert cv.collapsed_groups == {"performance"}

    def test_zC_all(self):
        cv = _state()
        _handle(cv, _ev("char", "z"))
        assert _handle(cv, _ev("char", "C")) is True
        assert cv.collapsed_groups == {"performance", "model"}

    def test_bracket_jump(self):
        cv = _state()
        assert _handle(cv, _ev("char", "]")) is True
        assert cv.selected == 2

    def test_group_header_rendered(self):
        from src.tui.ink.widgets.listview import ListView
        from src.tui.app.config_view import _ConfigGroupRow
        model = _model()
        _fiber, el = _render(model)
        found = None
        stack = [el]
        while stack:
            node = stack.pop()
            if getattr(node, "type", None) == ListView:
                found = node
                break
            stack.extend(getattr(node, "children", None) or [])
        assert found is not None
        headers = [
            it for it in found.props["items"] if isinstance(it, _ConfigGroupRow)
        ]
        assert any(h.group == "performance" for h in headers)
        child = found.props["renderItem"](headers[0], 0, False)
        assert "performance" in _text(child)


# ═══════════════════════════════════════════════════════════
# 2. 编辑 diff 预览
# ═══════════════════════════════════════════════════════════


class TestDiffPreview:

    def test_diff_runs(self):
        from src.tui.app.config_view import _diff_runs
        runs = _diff_runs("old", "new")
        text = "".join(r.text for r in runs)
        assert "old" in text and "new" in text
        assert "\u2192" in text

    def test_diff_same_dim(self):
        from src.tui.app.config_view import _diff_runs, _S_DIFF
        runs = _diff_runs("same", "same")
        assert not any(r.style is _S_DIFF for r in runs)

    def test_diff_changed_highlight(self):
        from src.tui.app.config_view import _diff_runs, _S_DIFF
        runs = _diff_runs("a", "b")
        assert any(r.style is _S_DIFF for r in runs)

    def test_diff_rendered_in_edit(self):
        model = _model(editing=True, edit_mode="input",
                       edit_key="A", edit_value="newval")
        _fiber, el = _render(model)
        text = _text(el)
        assert "\u65e7:" in text and "\u65b0:" in text
        assert "newval" in text


# ═══════════════════════════════════════════════════════════
# 3. 撤销历史面板
# ═══════════════════════════════════════════════════════════


class TestUndoPanel:

    def _cv_with_stack(self):
        cv = _state()
        cv.undo_stack = [
            ("A", "old-A", "old-A", "performance.a"),
            ("C", "old-C", "old-C", "model"),
        ]
        return cv

    def test_undo_entries_order(self):
        from src.tui.app.config_view import _undo_entries
        cv = self._cv_with_stack()
        rows = _undo_entries(cv)
        assert rows[0]["key"] == "C"  # 最新在前
        assert rows[1]["key"] == "A"

    def test_U_opens_panel(self):
        cv = self._cv_with_stack()
        assert _handle(cv, _ev("char", "U")) is True
        assert cv.editing is True and cv.edit_mode == "undo"

    def test_U_empty(self):
        cv = _state()
        assert _handle(cv, _ev("char", "U")) is True
        assert cv.editing is False
        assert "\u65e0\u53ef\u64a4\u9500" in cv.message

    def test_undo_to_returns_history(self, isolated_rc):
        from src.tui.app.config_view import _undo_to
        cv = self._cv_with_stack()
        entries = _entries()
        _undo_to(cv, entries, 1)  # 回退到最旧一条（撤销全部）
        assert cv.undo_stack == []
        assert entries[0]["value"] == "old-A"
        assert entries[2]["value"] == "old-C"

    def test_undo_panel_rendered(self):
        from src.tui.ink.widgets.listview import ListView
        model = _model(editing=True, edit_mode="undo")
        model.config_view.undo_stack = [
            ("A", "old-A", "old-A", "performance.a"),
        ]
        _fiber, el = _render(model)
        found = None
        stack = [el]
        while stack:
            node = stack.pop()
            if getattr(node, "type", None) == ListView:
                found = node
                break
            stack.extend(getattr(node, "children", None) or [])
        assert found is not None
        items = found.props["items"]
        assert items and items[0]["key"] == "A"
        child = found.props["renderItem"](items[0], 0, True)
        assert "old-A" in _text(child)


# ═══════════════════════════════════════════════════════════
# 4. 导出 / 导入
# ═══════════════════════════════════════════════════════════


class TestExportImport:

    def test_entries_to_map_excludes_sensitive(self):
        from src.tui.app.config_export import entries_to_map
        entries = _entries()
        entries[2]["sensitive"] = True
        out = entries_to_map(entries)
        assert "A" in out and "C" not in out

    def test_entries_to_json_roundtrip(self):
        from src.tui.app.config_export import entries_to_json, parse_import
        text = entries_to_json(_entries())
        mapping, err = parse_import(text)
        assert err == ""
        assert mapping["A"] == "v"

    def test_parse_import_plain(self):
        from src.tui.app.config_export import parse_import
        mapping, err = parse_import('{"A": "x"}')
        assert err == "" and mapping == {"A": "x"}

    def test_parse_import_bad(self):
        from src.tui.app.config_export import parse_import
        _m, err = parse_import("not json")
        assert err

    def test_write_export(self, tmp_path):
        from src.tui.app.config_export import write_export
        import os
        path = write_export(_entries(), directory=str(tmp_path))
        assert path.endswith(".json")
        assert os.path.exists(os.path.join(str(tmp_path), os.path.basename(path)))

    def test_e_key_exports(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        cv = _state()
        assert _handle(cv, _ev("char", "e")) is True
        assert "\u5df2\u5bfc\u51fa" in cv.message

    def test_i_key_starts_import(self):
        cv = _state()
        assert _handle(cv, _ev("char", "i")) is True
        assert cv.editing is True and cv.edit_mode == "import"

    def test_import_input_and_commit(self, isolated_rc, tmp_path):
        import json
        path = tmp_path / "in.json"
        path.write_text(json.dumps({"config": {"MODEL": "deepseek-v4-flash"}}),
                        encoding="utf-8")
        cv = _state()
        _handle(cv, _ev("char", "i"))
        cv.edit_value = str(path)
        assert _handle(cv, _ev("enter")) is True
        assert cv.editing is False
        assert "\u5bfc\u5165" in cv.message

    def test_apply_import_unknown_key(self, isolated_rc):
        from src.tui.app.config_export import apply_import
        applied, errors = apply_import({"__nope__": 1})
        assert applied == 0 and errors

    def test_apply_import_known(self, isolated_rc):
        from src.tui.app.config_export import apply_import
        from src.config.view_model import build_config_entries
        applied, errors = apply_import({"model": "deepseek-v4-flash"})
        assert applied == 1 and not errors
        # 写回生效
        entries = {e["path"]: e for e in build_config_entries()}
        assert entries["model"]["value"] == "deepseek-v4-flash"
