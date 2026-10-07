"""config 增强测试（2026-10-07 用户需求：更好的显示 / 操作 / 更多功能）。

覆盖：
  1. 配置项搜索（匹配文本 / 状态行计数 / 过滤模式）；
  2. 帮助面板（``?`` 开关 + 滚动 + 键位速查数据源）；
  3. 恢复默认值（``r``）；
  4. 撤销上次编辑（``u``，编辑与恢复默认均入栈）；
  5. 复制配置值（``y``，OSC52）；
  6. 头部显示配置文件路径来源。
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
    """隔离 RC 配置文件（临时目录）——写回不污染真实用户配置。"""
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


def _entries():
    return [
        {
            "key": "MODEL", "path": "model", "type": str,
            "value": "deepseek-v4-flash", "value_text": "deepseek-v4-flash",
            "default_text": "deepseek-v4-flash", "desc": "当前模型",
            "sensitive": False, "options": [("a", "")], "edit_kind": "select",
        },
        {
            "key": "TEMPERATURE", "path": "temperature", "type": float,
            "value": 0.2, "value_text": "0.2", "default_text": "0.2",
            "desc": "大模型温度", "sensitive": False,
            "options": None, "edit_kind": "input",
        },
        {
            "key": "api_key", "path": "api_key", "type": str,
            "value": "sk-test", "value_text": "sk-...test",
            "default_text": "", "desc": "API Key", "sensitive": True,
            "options": None, "edit_kind": "input",
        },
    ]


def _state(entries=None, **kw):
    return ConfigViewState(
        visible=True, seq=1, entries=entries if entries is not None else _entries(),
        rc_file=str(kw.pop("rc_file", "")),
        **kw,
    )


# ═══════════════════════════════════════════════════════════
# 1. 搜索 / 状态行
# ═══════════════════════════════════════════════════════════


class TestConfigSearch:

    def test_search_matches_path_and_desc(self):
        from src.tui.app.config_view import _config_search_matches
        entries = _entries()
        assert _config_search_matches(entries, "") == []
        assert _config_search_matches(entries, "model") == [0]
        assert _config_search_matches(entries, "\u6e29\u5ea6") == [1]
        assert _config_search_matches(entries, "API KEY") == [2]
        assert _config_search_matches(entries, "zzz") == []

    def test_search_matches_value_text(self):
        from src.tui.app.config_view import _config_search_matches
        assert _config_search_matches(_entries(), "sk-") == [2]

    def test_status_text_counts(self):
        from src.tui.app.config_view import _config_status_text
        cv = _state(search_pattern="model", search_idx=0, search_matches=[0])
        text = _config_status_text(cv, False, 1)
        assert "/model" in text and "1/1" in text

    def test_status_text_filter_marker(self):
        from src.tui.app.config_view import _config_status_text
        cv = _state(search_pattern="model", search_idx=0)
        assert "[\u8fc7\u6ee4]" in _config_status_text(cv, True, 1)

    def test_status_text_message_and_error(self):
        from src.tui.app.config_view import _config_status_text
        cv = _state(message="已更新 model = x")
        assert "\u5df2\u66f4\u65b0" in _config_status_text(cv, False, 0)
        cv.edit_error = "写入失败"
        assert "\u5199\u5165\u5931\u8d25" in _config_status_text(cv, False, 0)

    def test_cycle_filter_requires_search(self):
        from src.tui.app.config_view import _cycle_config_filter
        cv = _state()
        _cycle_config_filter(cv)
        assert cv.search_filter is False
        assert "\u9700\u5148\u641c\u7d22" in cv.message

    def test_cycle_filter_toggles(self):
        from src.tui.app.config_view import _cycle_config_filter
        cv = _state(search_pattern="model", search_matches=[0])
        _cycle_config_filter(cv)
        assert cv.search_filter is True
        _cycle_config_filter(cv)
        assert cv.search_filter is False


# ═══════════════════════════════════════════════════════════
# 2. 恢复默认 / 撤销 / 复制
# ═══════════════════════════════════════════════════════════


class TestResetUndoCopy:

    def test_reset_entry_default(self, isolated_rc, monkeypatch):
        from src.tui.app import config_view as cvm
        entries = _entries()
        cv = _state(entries)
        _reset_entry_default = cvm._reset_entry_default
        _reset_entry_default(cv, entries, 1)
        assert entries[1]["value"] == 0.2  # 已是默认值
        assert cv.undo_stack  # 入栈
        assert "\u5df2\u6062\u590d\u9ed8\u8ba4" in cv.message

    def test_undo_stack_empty_message(self):
        from src.tui.app.config_view import _undo_last
        cv = _state()
        _undo_last(cv, cv.entries)
        assert "\u65e0\u53ef\u64a4\u9500" in cv.message

    def test_undo_restores_old_value(self, isolated_rc):
        from src.tui.app.config_view import _undo_last
        entries = _entries()
        cv = _state(entries)
        cv.undo_stack = [(
            "TEMPERATURE", 0.9, "0.9", "temperature",
        )]
        entries[1]["value"] = 0.2
        entries[1]["value_text"] = "0.2"
        _undo_last(cv, entries)
        assert entries[1]["value"] == 0.9
        assert entries[1]["value_text"] == "0.9"
        assert "\u5df2\u64a4\u9500" in cv.message
        assert cv.undo_stack == []

    def test_push_undo_bounded(self):
        from src.tui.app.config_view import _UNDO_MAX, _push_undo
        cv = _state()
        entry = cv.entries[0]
        for i in range(_UNDO_MAX + 5):
            _push_undo(cv, entry, i)
        assert len(cv.undo_stack) == _UNDO_MAX

    def test_copy_entry(self, monkeypatch):
        from src.tui.app.config_view import _copy_entry
        captured = {}
        monkeypatch.setattr(
            "src.tui._screen.set_clipboard",
            lambda text: captured.setdefault("text", text) or True,
        )
        cv = _state()
        _copy_entry(cv, cv.entries[0])
        assert captured["text"] == "model = deepseek-v4-flash"
        assert "\u5df2\u590d\u5236" in cv.message

    def test_copy_entry_none(self):
        from src.tui.app.config_view import _copy_entry
        cv = _state()
        _copy_entry(cv, None)
        assert "\u65e0\u53ef\u590d\u5236" in cv.message


# ═══════════════════════════════════════════════════════════
# 3. 事件处理（键位）
# ═══════════════════════════════════════════════════════════


class TestConfigEventKeys:

    def _handle(self, cv, event, **kw):
        from src.tui.app.config_view import _handle_config_event
        entries = cv.entries
        return _handle_config_event(
            cv, entries, event, visible=True, total=len(entries),
            all_entries=entries, view_map=None, pane_vh=10,
        )

    def test_slash_enters_search(self):
        cv = _state()
        assert self._handle(cv, _ev("char", "/")) is True
        assert cv.search_mode is True

    def test_search_input_and_execute(self, isolated_rc):
        cv = _state()
        self._handle(cv, _ev("char", "/"))
        self._handle(cv, _ev("char", "m"))
        self._handle(cv, _ev("char", "o"))
        assert cv.search_query == "mo"
        assert self._handle(cv, _ev("enter")) is True
        assert cv.search_mode is False
        assert cv.search_pattern == "mo"
        assert cv.search_matches == [0]

    def test_search_backspace_and_escape(self):
        cv = _state()
        self._handle(cv, _ev("char", "/"))
        self._handle(cv, _ev("char", "a"))
        assert self._handle(cv, _ev("backspace")) is True
        assert cv.search_query == ""
        assert self._handle(cv, _ev("escape")) is True
        assert cv.search_mode is False

    def test_next_match_navigates(self):
        cv = _state(search_pattern="m", search_matches=[0, 1], search_idx=0)
        assert self._handle(cv, _ev("char", "n")) is True
        assert cv.search_idx == 1
        assert cv.selected == 1
        assert self._handle(cv, _ev("char", "N")) is True
        assert cv.search_idx == 0

    def test_filter_key(self):
        cv = _state(search_pattern="m", search_matches=[0])
        assert self._handle(cv, _ev("char", "f")) is True
        assert cv.search_filter is True

    def test_help_toggle_and_close(self):
        cv = _state()
        assert self._handle(cv, _ev("char", "?")) is True
        assert cv.help_open is True
        assert self._handle(cv, _ev("escape")) is True
        assert cv.help_open is False

    def test_help_scroll(self):
        cv = _state(help_open=True)
        self._handle(cv, _ev("arrow_down"))
        assert cv.help_scroll == 1
        self._handle(cv, _ev("page_down"))
        assert cv.help_scroll == 11
        self._handle(cv, _ev("char", "g"))
        assert cv.help_scroll == 0

    def test_reset_key(self, isolated_rc):
        cv = _state()
        cv.selected = 1
        assert self._handle(cv, _ev("char", "r")) is True
        assert cv.undo_stack

    def test_undo_key(self, isolated_rc):
        cv = _state()
        cv.undo_stack = [("TEMPERATURE", 0.7, "0.7", "temperature")]
        assert self._handle(cv, _ev("char", "u")) is True
        assert cv.entries[1]["value"] == 0.7

    def test_copy_key(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            "src.tui._ui_stub" if False else "src.tui._screen.set_clipboard",
            lambda text: captured.setdefault("text", text) or True,
        )
        cv = _state()
        assert self._handle(cv, _ev("char", "y")) is True
        assert "model" in captured["text"]

    def test_editing_ignores_enhance_keys(self):
        """编辑模式下增强键不生效（字符进入编辑缓冲）。"""
        cv = _state(editing=True, edit_mode="input", edit_key="TEMPERATURE",
                    edit_value="")
        assert self._handle(cv, _ev("char", "7")) is True
        assert cv.edit_value == "7"


# ═══════════════════════════════════════════════════════════
# 4. 组件渲染（头部来源 / 帮助面板 / 过滤视图）
# ═══════════════════════════════════════════════════════════


class TestConfigViewRender:

    def _render(self, model, width=100):
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

    def _model(self, **kw):
        from src.tui.app.model import AppModel
        model = AppModel()
        model.config_view = _state(**kw)
        model.fullscreen = "config"
        return model

    def _text(self, el) -> str:
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

    def test_header_shows_source(self):
        model = self._model(rc_file="/tmp/chatrc.json")
        _fiber, el = self._render(model)
        assert "/tmp/chatrc.json" in self._text(el)

    def test_help_panel_renders(self):
        model = self._model(help_open=True)
        _fiber, el = self._render(model)
        text = self._text(el)
        assert "\u5e2e\u52a9\u9762\u677f" in text
        assert "Enter" in text

    def test_status_line_renders_search(self):
        model = self._model(search_pattern="model", search_matches=[0])
        _fiber, el = self._render(model)
        assert "/model" in self._text(el)

    def test_filter_view_reduces_items(self):
        from src.tui.ink.widgets.listview import ListView
        model = self._model(
            search_pattern="model", search_matches=[0], search_filter=True,
        )
        _fiber, el = self._render(model)
        found = None
        stack = [el]
        while stack:
            node = stack.pop()
            if getattr(node, "type", None) == ListView:
                found = node
                break
            for child in getattr(node, "children", None) or []:
                stack.append(child)
        assert found is not None
        assert len(found.props["items"]) == 1
