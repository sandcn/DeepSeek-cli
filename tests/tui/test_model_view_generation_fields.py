"""ModelView（/models）温度 / 思考级别设置测试。

2026-10-17 用户需求「/models 支持设置对应的温度跟思考级别，删除以前全局
的命令」：表单新增「温度」「思考级别」字段；保存后写入模型档案，API 请求
按当前档案生效（空值沿用全局默认）。全局 /temperature 与 /reasoning 命令
已删除。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import src.config.model_profiles as mp
from src.config.model_profiles import FIELD_KEYS
from src.tui.app._state_types import ModelViewState
from src.tui.app.model import AppModel
from src.tui.app.model_view import (
    ModelView,
    _detail_runs,
    _field_value_display,
    _handle_model_event,
)


@pytest.fixture
def isolated_rc(monkeypatch, tmp_path):
    """隔离 RC 配置文件（临时目录）——保存/应用不污染真实用户配置。"""
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
    return SimpleNamespace(kind=kind, char=char, modifier=0, keycode=0, raw=b"")


def _state(entries=None):
    return ModelViewState(visible=True, entries=list(entries or []))


def _dispatch(mv, event, pane_vh=10):
    entries = list(getattr(mv, "entries", None) or [])
    return _handle_model_event(
        mv, entries, event, visible=True, total=len(entries),
        all_entries=entries, view_map=None, pane_vh=pane_vh,
    )


def _open_new_form(mv, model="m-new"):
    _dispatch(mv, _ev("char", "a"))
    assert mv.editing is True
    mv.form_values["model"] = model
    return mv


def _type_text(mv, text: str):
    for ch in text:
        _dispatch(mv, _ev("char", ch))


# ═══════════════════════════════════════════════════════════
# 表单显示
# ═══════════════════════════════════════════════════════════

class TestFormDisplay:

    def test_empty_generation_fields_show_global_default(self, monkeypatch):
        monkeypatch.setattr("src.config.model_profiles.thinking_enabled", lambda rc=None: False)
        mv = _state()
        mv.form_values = mp.empty_profile()
        assert _field_value_display(mv, "temperature") == "（沿用全局默认）"
        assert _field_value_display(mv, "reasoning_effort") == "（沿用全局默认）"
        mv.form_values["temperature"] = "0.7"
        assert _field_value_display(mv, "temperature") == "0.7"

    def test_render_form_contains_generation_fields(self):
        from src.tui.ink import h, renderToString
        mv = _state()
        _open_new_form(mv)
        m = AppModel()
        m.model_view = mv
        m.fullscreen = "model"
        out = renderToString(h(ModelView, {"model": m, "width": 80}), {"columns": 80})
        assert "温度" in out and "思考级别" in out

    def test_edit_preserves_generation_values(self, isolated_rc):
        mp.save_profiles([mp.normalize_profile({
            "model": "m1", "provider": "deepseek",
            "temperature": "0.65", "reasoning_effort": "high",
        })])
        mv = ModelViewState(visible=True, entries=mp.build_model_entries())
        mv.selected = 0
        _dispatch(mv, _ev("char", "e"))
        assert mv.form_values["temperature"] == "0.65"
        assert mv.form_values["reasoning_effort"] == "high"


# ═══════════════════════════════════════════════════════════
# 思考级别选择界面
# ═══════════════════════════════════════════════════════════

class TestReasoningSelect:

    def test_select_options_include_global_default(self):
        mv = _state()
        _open_new_form(mv)
        mv.form_selected = list(FIELD_KEYS).index("reasoning_effort")
        assert _dispatch(mv, _ev("enter")) is True
        assert mv.edit_mode == "select"
        opts = list(mv.form_select_options)
        assert opts == ["", "low", "medium", "high", "max"]
        # 空值（沿用全局默认）为默认高亮
        assert mv.form_select_index == 0
        assert "沿用全局默认" in mv.form_select_desc[0]

    def test_choose_high_writes_form_value(self):
        mv = _state()
        _open_new_form(mv)
        mv.form_selected = list(FIELD_KEYS).index("reasoning_effort")
        _dispatch(mv, _ev("enter"))
        opts = list(mv.form_select_options)
        mv.form_select_index = opts.index("high")
        assert _dispatch(mv, _ev("enter")) is True
        assert mv.edit_mode == "form"
        assert mv.form_values["reasoning_effort"] == "high"
        assert "思考级别: high" in mv.message

    def test_select_does_not_touch_base_url(self):
        mv = _state()
        _open_new_form(mv)
        base = mv.form_values["base_url"]
        mv.form_selected = list(FIELD_KEYS).index("reasoning_effort")
        _dispatch(mv, _ev("enter"))
        mv.form_select_index = 2
        _dispatch(mv, _ev("enter"))
        assert mv.form_values["base_url"] == base

    def test_select_renderer_shows_default_label(self):
        from src.tui.app.model_view import _make_select_renderer
        render = _make_select_renderer(60, 14, ["沿用全局默认（当前 max）"])
        el = render("", 0, True)
        text = "".join(r.text for r in (el.props or {}).get("styled") or [])
        assert "沿用全局默认" in text


# ═══════════════════════════════════════════════════════════
# 温度输入 / 保存
# ═══════════════════════════════════════════════════════════

class TestTemperatureAndSave:

    def test_input_temperature(self):
        mv = _state()
        _open_new_form(mv)
        mv.form_selected = list(FIELD_KEYS).index("temperature")
        _dispatch(mv, _ev("enter"))
        assert mv.edit_mode == "field"
        _type_text(mv, "0.7")
        _dispatch(mv, _ev("enter"))
        assert mv.edit_mode == "form"
        assert mv.form_values["temperature"] == "0.7"

    def test_save_generation_values(self, isolated_rc):
        mv = _state()
        _open_new_form(mv)
        mv.form_values["temperature"] = "0.75"
        mv.form_values["reasoning_effort"] = "high"
        _dispatch(mv, _ev("char", "s"))
        assert not mv.editing and "已新增" in mv.message
        saved = mp.load_profiles()
        assert saved[0]["temperature"] == "0.75"
        assert saved[0]["reasoning_effort"] == "high"

    def test_save_empty_inherits_global(self, isolated_rc):
        mv = _state()
        _open_new_form(mv)
        _dispatch(mv, _ev("char", "s"))
        saved = mp.load_profiles()
        assert saved[0]["temperature"] == ""
        assert saved[0]["reasoning_effort"] == ""
        entry = mp.build_model_entries()[0]
        assert entry["effective_temperature"] == 0.2
        assert entry["effective_reasoning_effort"] == "max"

    @pytest.mark.parametrize("raw", ["3", "-1", "abc", "nan"])
    def test_save_rejects_invalid_temperature(self, isolated_rc, raw):
        mv = _state()
        _open_new_form(mv)
        mv.form_values["temperature"] = raw
        _dispatch(mv, _ev("char", "s"))
        assert mv.editing
        assert "温度" in mv.edit_error
        assert mp.load_profiles() == []

    def test_save_rejects_invalid_effort(self, isolated_rc):
        mv = _state()
        _open_new_form(mv)
        mv.form_values["reasoning_effort"] = "ultra"
        _dispatch(mv, _ev("char", "s"))
        assert mv.editing
        assert "思考级别" in mv.edit_error


# ═══════════════════════════════════════════════════════════
# 详情行
# ═══════════════════════════════════════════════════════════

class TestDetailGeneration:

    def test_detail_shows_override_values(self):
        entry = {
            "model": "m", "provider": "deepseek", "effective_base_url": "",
            "temperature": "0.7", "effective_temperature": 0.7,
            "reasoning_effort": "high", "effective_reasoning_effort": "high",
        }
        text = "".join(r.text for r in _detail_runs(entry, 200))
        assert "温度 0.7" in text and "(默认)" not in text
        assert "思考 high" in text

    def test_detail_marks_inherited_values(self):
        entry = {
            "model": "m", "provider": "deepseek", "effective_base_url": "",
            "temperature": "", "effective_temperature": 0.2,
            "reasoning_effort": "", "effective_reasoning_effort": "max",
        }
        text = "".join(r.text for r in _detail_runs(entry, 200))
        assert "温度 0.2(默认)" in text
        assert "思考 max(默认)" in text

    def test_component_renders_detail_generation(self):
        from src.tui.ink import h, renderToString
        entry = {
            "kind": "profile", "index": 0, "name": "测试模型",
            "model": "deepseek-v4-pro", "base_url": "",
            "effective_base_url": "https://api.deepseek.com/v1",
            "api_key": "sk-test", "provider": "deepseek",
            "temperature": "0.7", "reasoning_effort": "high",
            "effective_temperature": 0.7, "effective_reasoning_effort": "high",
            "current": True, "editable": True, "hint": "DeepSeek 官方",
        }
        mv = _state([entry])
        m = AppModel()
        m.model_view = mv
        m.fullscreen = "model"
        out = renderToString(h(ModelView, {"model": m, "width": 240}), {"columns": 240})
        assert "温度 0.7" in out
        assert "思考 high" in out


# ═══════════════════════════════════════════════════════════
# 温度是否生效（思考模式开启 → 提示「不生效」）
# ═══════════════════════════════════════════════════════════

class TestTemperatureEffectNotice:
    """DeepSeek 官方文档：思考模式不支持 temperature（设置不报错但被忽略）。"""

    def test_field_value_marked_when_thinking_enabled(self, monkeypatch):
        monkeypatch.setattr("src.config.model_profiles.thinking_enabled", lambda rc=None: True)
        mv = _state()
        mv.form_values = mp.empty_profile()
        mv.form_values["temperature"] = "0.7"
        assert _field_value_display(mv, "temperature") == "0.7（思考模式开启，不生效）"

    def test_field_value_plain_when_thinking_disabled(self, monkeypatch):
        monkeypatch.setattr("src.config.model_profiles.thinking_enabled", lambda rc=None: False)
        mv = _state()
        mv.form_values = mp.empty_profile()
        mv.form_values["temperature"] = "0.7"
        assert _field_value_display(mv, "temperature") == "0.7"

    def test_form_hint_when_thinking_enabled(self, monkeypatch):
        monkeypatch.setattr("src.config.model_profiles.thinking_enabled", lambda rc=None: True)
        mv = _state()
        _open_new_form(mv)
        assert "不生效" in mv.message and "Ctrl+T" in mv.message
        mv.form_selected = list(FIELD_KEYS).index("temperature")
        _dispatch(mv, _ev("enter"))
        _type_text(mv, "0.7")
        _dispatch(mv, _ev("enter"))
        assert "不生效" in mv.message

    def test_form_hint_cleared_when_thinking_disabled(self, monkeypatch):
        monkeypatch.setattr("src.config.model_profiles.thinking_enabled", lambda rc=None: False)
        mv = _state()
        _open_new_form(mv)
        assert mv.message == ""

    def test_detail_marks_ineffective_temperature(self, monkeypatch):
        monkeypatch.setattr("src.config.model_profiles.thinking_enabled", lambda rc=None: True)
        entry = {
            "model": "m", "provider": "deepseek", "effective_base_url": "",
            "temperature": "0.7", "effective_temperature": 0.7,
            "reasoning_effort": "high", "effective_reasoning_effort": "high",
        }
        text = "".join(r.text for r in _detail_runs(entry, 300))
        assert "不生效" in text

    def test_save_message_mentions_ineffective_temperature(self, monkeypatch, isolated_rc):
        monkeypatch.setattr("src.config.model_profiles.thinking_enabled", lambda rc=None: True)
        mv = _state()
        _open_new_form(mv)
        mv.form_values["temperature"] = "0.75"
        _dispatch(mv, _ev("char", "s"))
        assert "已新增" in mv.message and "不生效" in mv.message
