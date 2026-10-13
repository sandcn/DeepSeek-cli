"""模型名候选（/models 界面「模型名」从提供商内置模型列表选择）测试。

2026-10-13 用户需求：
  「/models 界面可以选择对应提供商的模型名」——在模型选择器里新增/编辑
  模型档案时，进入「模型名」字段即可从**该提供商的内置模型列表**中
  ``↑``/``↓`` 选择（含「默认」与计费单价说明），同时仍可直接键入自定义
  模型名（兼容自建 / 新上线模型）。

覆盖：
  1. ``config.model_profiles`` 纯逻辑：``provider_models`` /
     ``default_model_for`` / ``provider_model_choices``；
  2. ``ModelViewState`` 候选字段默认值与 ``reset_edit_state``；
  3. 事件流：候选加载 / ↑↓ 选择 / 自定义输入同步高亮 / Enter 提交 /
     Esc 取消 / 提供商选择后的引导提示 / ``custom`` 无候选零回归 /
     粘贴同步高亮；
  4. 组件渲染（``renderToString``）：候选列表与提示；
  5. 保存：候选选择后写回的档案模型名正确；
  6. 键位数据表新增候选说明。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import src.config.model_profiles as mp
from src.tui.app._state_types import ModelViewState
from src.tui.app.model import AppModel
from src.tui.app.model_view import ModelView, _handle_model_event, _handle_model_paste


# ── 测试辅助 ──────────────────────────────────────────────

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


def _open_new_form(mv):
    """浏览模式按 ``a`` 打开新增表单（provider 预填默认提供商）。"""
    _dispatch(mv, _ev("char", "a"))
    assert mv.editing is True
    return mv


def _enter_model_field(mv):
    """定位到「模型名」字段并进入编辑（加载候选）。"""
    from src.config.model_profiles import FIELD_KEYS
    mv.form_selected = list(FIELD_KEYS).index("model")
    _dispatch(mv, _ev("enter"))
    assert mv.edit_mode == "field"


# ═══════════════════════════════════════════════════════════
# 1. model_profiles 纯逻辑
# ═══════════════════════════════════════════════════════════

class TestProviderModels:

    def test_deepseek_models(self):
        models = mp.provider_models("deepseek")
        assert models and "deepseek-flash" in models and "deepseek-v4-pro" in models
        # 与内置元数据同源（顺序一致）
        from src.config.defaults import PROVIDERS
        assert models == list(PROVIDERS["deepseek"]["models"])

    def test_custom_and_unknown_are_empty(self):
        assert mp.provider_models("custom") == []
        assert mp.provider_models("不存在的提供商") == []
        assert mp.provider_models("") == []
        assert mp.provider_models(None) == []

    def test_dedup_and_strip(self, monkeypatch):
        from src.config import defaults as d
        monkeypatch.setitem(d.PROVIDERS, "t", {"base_url": "", "models": ["a", " ", "a", "b"]})
        assert mp.provider_models("t") == ["a", "b"]

    def test_returns_copy(self):
        first = mp.provider_models("deepseek")
        first.append("mutated")
        assert "mutated" not in mp.provider_models("deepseek")

    def test_default_model_for(self):
        assert mp.default_model_for("deepseek") == "deepseek-v4-pro"
        assert mp.default_model_for("glm") == "glm-5.2"
        assert mp.default_model_for("custom") == ""
        assert mp.default_model_for("unknown-provider") == ""


    def test_model_field_hint_mentions_choices(self):
        hint = (mp.field_by_key("model") or {}).get("hint", "")
        assert "内置模型" in hint
        # 「模型名」仍是可输入字段（候选 + 自定义混合），而非纯选择字段
        assert (mp.field_by_key("model") or {}).get("kind") == "input"


class TestProviderModelChoices:

    def test_shape_and_default_marker(self):
        choices = mp.provider_model_choices("deepseek")
        models = [c[0] for c in choices]
        assert models == mp.provider_models("deepseek")
        descs = dict(choices)
        assert "默认" in descs["deepseek-v4-pro"]
        assert "默认" not in descs["deepseek-flash"]

    def test_price_in_description(self):
        descs = dict(mp.provider_model_choices("deepseek"))
        assert "$0.3/$1.2" in descs["deepseek-flash"]
        assert "$0.55/$2.19" in descs["deepseek-v4-pro"]

    def test_fallback_description_without_price(self):
        descs = dict(mp.provider_model_choices("glm"))
        assert descs == {"glm-5.2": "默认"}

    def test_custom_or_unknown_empty(self):
        assert mp.provider_model_choices("custom") == []
        assert mp.provider_model_choices("") == []

    def test_anthropic_choices(self):
        models = [c[0] for c in mp.provider_model_choices("anthropic")]
        assert "claude-sonnet-4-6" in models and "claude-opus-4-6" in models


# ═══════════════════════════════════════════════════════════
# 2. ModelViewState 候选字段
# ═══════════════════════════════════════════════════════════

class TestStateFields:

    def test_defaults(self):
        st = ModelViewState()
        assert st.form_choice_options == []
        assert st.form_choice_desc == []
        assert st.form_choice_index == -1
        assert st.form_choice_active is False

    def test_reset_edit_state_clears_choices(self):
        st = ModelViewState()
        st.form_choice_options = ["a"]
        st.form_choice_desc = ["d"]
        st.form_choice_index = 0
        st.form_choice_active = True
        st.reset_edit_state()
        assert st.form_choice_options == []
        assert st.form_choice_desc == []
        assert st.form_choice_index == -1
        assert st.form_choice_active is False


# ═══════════════════════════════════════════════════════════
# 3. 事件流
# ═══════════════════════════════════════════════════════════

class TestChoiceEvents:

    def test_choices_loaded_for_new_form(self, isolated_rc):
        mv = _open_new_form(_state())
        assert mv.form_values["provider"] == "deepseek"
        _enter_model_field(mv)
        assert mv.form_choice_active is True
        assert mv.form_choice_options == mp.provider_models("deepseek")
        assert len(mv.form_choice_desc) == len(mv.form_choice_options)
        # 输入值为空 → 无候选高亮（-1 = 等待选择 / 自定义）
        assert mv.form_choice_index == -1

    def test_no_choices_for_input_field(self, isolated_rc):
        mv = _open_new_form(_state())
        from src.config.model_profiles import FIELD_KEYS
        mv.form_selected = list(FIELD_KEYS).index("api_key")
        _dispatch(mv, _ev("enter"))
        assert mv.edit_mode == "field"
        assert mv.form_choice_active is False
        assert mv.form_choice_options == []

    def test_no_choices_for_custom_provider(self, isolated_rc):
        mv = _open_new_form(_state())
        mv.form_values["provider"] = "custom"
        _enter_model_field(mv)
        assert mv.form_choice_active is False
        assert mv.form_choice_options == []
        # 纯手动输入（零回归）
        _dispatch(mv, _ev("char", "m"))
        assert mv.form_edit_value == "m"

    def test_arrow_down_selects_first_then_next(self, isolated_rc):
        mv = _open_new_form(_state())
        _enter_model_field(mv)
        opts = list(mv.form_choice_options)
        assert _dispatch(mv, _ev("arrow_down")) is True
        assert mv.form_edit_value == opts[0] and mv.form_choice_index == 0
        _dispatch(mv, _ev("arrow_down"))
        assert mv.form_edit_value == opts[1] and mv.form_choice_index == 1
        _dispatch(mv, _ev("arrow_up"))
        assert mv.form_edit_value == opts[0] and mv.form_choice_index == 0
        # 首项再上移 → 钳制（不越界）
        _dispatch(mv, _ev("arrow_up"))
        assert mv.form_choice_index == 0 and mv.form_edit_value == opts[0]

    def test_arrow_up_from_custom_jumps_to_last(self, isolated_rc):
        mv = _open_new_form(_state())
        _enter_model_field(mv)
        opts = list(mv.form_choice_options)
        _dispatch(mv, _ev("arrow_up"))
        assert mv.form_choice_index == len(opts) - 1
        assert mv.form_edit_value == opts[-1]

    def test_upper_bound_clamped(self, isolated_rc):
        mv = _open_new_form(_state())
        _enter_model_field(mv)
        opts = list(mv.form_choice_options)
        for _ in range(len(opts) + 3):
            _dispatch(mv, _ev("arrow_down"))
        assert mv.form_choice_index == len(opts) - 1
        assert mv.form_edit_value == opts[-1]

    def test_typing_custom_model_clears_highlight(self, isolated_rc):
        mv = _open_new_form(_state())
        _enter_model_field(mv)
        _dispatch(mv, _ev("arrow_down"))
        assert mv.form_choice_index == 0
        # 直接键入自定义模型名 → 候选高亮清除（自定义）
        _dispatch(mv, _ev("ctrl_key", "\x15"))  # Ctrl+U 清空
        assert mv.form_edit_value == "" and mv.form_choice_index == -1
        for ch in "my-model":
            _dispatch(mv, _ev("char", ch))
        assert mv.form_edit_value == "my-model"
        assert mv.form_choice_index == -1

    def test_typing_exact_candidate_highlights_it(self, isolated_rc):
        mv = _open_new_form(_state())
        _enter_model_field(mv)
        target = mv.form_choice_options[0]
        for ch in target:
            _dispatch(mv, _ev("char", ch))
        assert mv.form_edit_value == target
        assert mv.form_choice_index == 0

    def test_enter_commits_selected_candidate(self, isolated_rc):
        mv = _open_new_form(_state())
        _enter_model_field(mv)
        opts = list(mv.form_choice_options)
        _dispatch(mv, _ev("arrow_down"))
        _dispatch(mv, _ev("arrow_down"))
        assert _dispatch(mv, _ev("enter")) is True
        assert mv.edit_mode == "form"
        assert mv.form_values["model"] == opts[1]
        # 提交后候选状态清理
        assert mv.form_choice_options == [] and mv.form_choice_active is False
        assert mv.form_choice_index == -1

    def test_escape_cancels_and_clears_choices(self, isolated_rc):
        mv = _open_new_form(_state())
        _enter_model_field(mv)
        _dispatch(mv, _ev("arrow_down"))
        assert _dispatch(mv, _ev("escape")) is True
        assert mv.edit_mode == "form"
        assert mv.form_edit_value == "" and mv.form_choice_index == -1
        assert mv.form_choice_active is False
        # 表单仍处于编辑态（未退出档案表单）
        assert mv.editing is True

    def test_existing_value_highlights_matching_candidate(self, isolated_rc):
        model = mp.provider_models("deepseek")[1]
        mp.save_profiles([mp.normalize_profile({"provider": "deepseek", "model": model})])
        mv = ModelViewState(visible=True, entries=mp.build_model_entries())
        mv.selected = 0
        _dispatch(mv, _ev("char", "e"))
        _enter_model_field(mv)
        assert mv.form_edit_value == model
        assert mv.form_choice_index == mv.form_choice_options.index(model)

    def test_choices_follow_provider_change(self, isolated_rc):
        mp.save_profiles([mp.normalize_profile({"provider": "custom", "model": "m"})])
        mv = ModelViewState(visible=True, entries=mp.build_model_entries())
        mv.selected = 0
        _dispatch(mv, _ev("char", "e"))
        mv.form_values["provider"] = "anthropic"
        _enter_model_field(mv)
        assert mv.form_choice_options == mp.provider_models("anthropic")

    def test_paste_syncs_highlight(self, isolated_rc):
        mv = _open_new_form(_state())
        _enter_model_field(mv)
        target = mv.form_choice_options[0]
        assert _handle_model_paste(mv, True, target) is True
        assert mv.form_edit_value == target
        assert mv.form_choice_index == 0
        assert _handle_model_paste(mv, True, "-x") is True
        assert mv.form_edit_value == target + "-x"
        assert mv.form_choice_index == -1

    def test_provider_select_hints_candidate_count(self, isolated_rc):
        mv = _open_new_form(_state())
        mv.form_selected = 0
        _dispatch(mv, _ev("enter"))  # 进入提供商选择界面
        assert mv.edit_mode == "select"
        opts = list(mv.form_select_options)
        mv.form_select_index = opts.index("deepseek")
        _dispatch(mv, _ev("enter"))
        assert mv.edit_mode == "form"
        assert mv.form_values["provider"] == "deepseek"
        n = len(mp.provider_models("deepseek"))
        assert "内置模型" in mv.message and str(n) in mv.message

    def test_provider_select_hint_without_builtin_models(self, isolated_rc):
        mv = _open_new_form(_state())
        mv.form_selected = 0
        _dispatch(mv, _ev("enter"))
        opts = list(mv.form_select_options)
        mv.form_select_index = opts.index("custom")
        _dispatch(mv, _ev("enter"))
        assert "无内置模型" in mv.message

    def test_duplicate_keeps_choices_cleared_until_field(self, isolated_rc):
        mp.save_profiles([mp.normalize_profile({"provider": "deepseek", "model": "m1"})])
        mv = ModelViewState(visible=True, entries=mp.build_model_entries())
        mv.selected = 0
        _dispatch(mv, _ev("char", "c"))  # 复制为副本
        assert mv.editing is True and mv.form_choice_active is False
        _enter_model_field(mv)
        assert mv.form_choice_active is True


# ═══════════════════════════════════════════════════════════
# 4. 渲染
# ═══════════════════════════════════════════════════════════

class TestRender:

    def _render(self, mv, width=100):
        from src.tui.ink import h, renderToString
        m = AppModel()
        m.model_view = mv
        m.fullscreen = "model"
        return renderToString(h(ModelView, {"model": m, "width": width}), {"columns": width})

    def test_candidate_list_rendered(self, isolated_rc):
        mv = _open_new_form(_state())
        _enter_model_field(mv)
        out = self._render(mv)
        for model in mp.provider_models("deepseek"):
            assert model in out
        assert "选择内置模型候选" in out
        assert "自定义模型名" in out
        assert "模型名" in out

    def test_highlight_marker_on_selected_candidate(self, isolated_rc):
        mv = _open_new_form(_state())
        _enter_model_field(mv)
        _dispatch(mv, _ev("arrow_down"))
        out = self._render(mv)
        target = mv.form_choice_options[0]
        line = [ln for ln in out.splitlines() if target in ln]
        assert line and "\u25b6" in line[0]

    def test_custom_provider_falls_back_to_form_fields(self, isolated_rc):
        mv = _open_new_form(_state())
        mv.form_values["provider"] = "custom"
        _enter_model_field(mv)
        out = self._render(mv)
        # 无候选 → 主区仍是表单字段列表（既有渲染，零回归）
        assert "提供商" in out and "API 密钥" in out
        assert "选择内置模型候选" not in out

    def test_no_choice_hint_for_other_fields(self, isolated_rc):
        mv = _open_new_form(_state())
        from src.config.model_profiles import FIELD_KEYS
        mv.form_selected = list(FIELD_KEYS).index("api_key")
        _dispatch(mv, _ev("enter"))
        out = self._render(mv)
        assert "选择内置模型候选" not in out

    def test_form_footer_shows_provider_hint(self, isolated_rc):
        mv = _open_new_form(_state())
        mv.form_selected = 0
        _dispatch(mv, _ev("enter"))
        opts = list(mv.form_select_options)
        mv.form_select_index = opts.index("deepseek")
        _dispatch(mv, _ev("enter"))
        assert mv.edit_mode == "form"
        out = self._render(mv)
        assert "内置模型选择" in out


# ═══════════════════════════════════════════════════════════
# 5. 保存
# ═══════════════════════════════════════════════════════════

class TestSave:

    def test_save_selected_candidate(self, isolated_rc):
        mv = _open_new_form(_state())
        _enter_model_field(mv)
        _dispatch(mv, _ev("arrow_down"))
        selected = mv.form_edit_value
        _dispatch(mv, _ev("enter"))
        mv.form_values["api_key"] = "sk-test"
        _dispatch(mv, _ev("char", "s"))
        assert not mv.editing
        saved = mp.load_profiles()
        assert len(saved) == 1
        assert saved[0]["model"] == selected == mp.provider_models("deepseek")[0]
        assert saved[0]["provider"] == "deepseek"
        assert saved[0]["api_key"] == "sk-test"

    def test_save_custom_typed_model(self, isolated_rc):
        mv = _open_new_form(_state())
        _enter_model_field(mv)
        _dispatch(mv, _ev("ctrl_key", "\x15"))
        for ch in "my-own-model":
            _dispatch(mv, _ev("char", ch))
        _dispatch(mv, _ev("enter"))
        _dispatch(mv, _ev("char", "s"))
        saved = mp.load_profiles()
        assert saved[0]["model"] == "my-own-model"

    def test_edit_existing_switch_to_candidate(self, isolated_rc):
        mp.save_profiles([mp.normalize_profile({"provider": "glm", "model": "glm-5.2"})])
        mv = ModelViewState(visible=True, entries=mp.build_model_entries())
        mv.selected = 0
        _dispatch(mv, _ev("char", "e"))
        mv.form_values["provider"] = "deepseek"
        _enter_model_field(mv)
        _dispatch(mv, _ev("arrow_down"))
        picked = mv.form_edit_value
        _dispatch(mv, _ev("enter"))
        _dispatch(mv, _ev("char", "s"))
        saved = mp.load_profiles()
        assert saved[0]["model"] == picked
        assert saved[0]["provider"] == "deepseek"


# ═══════════════════════════════════════════════════════════
# 6. 键位数据表
# ═══════════════════════════════════════════════════════════

class TestKeymap:

    def test_choice_key_documented(self):
        from src.presentation_data import MODEL_KEYMAP_DATA
        rows = [item for item in MODEL_KEYMAP_DATA if item["keys"] == "\u2191 / \u2193"]
        assert rows, "缺少模型名候选键位说明"
        assert any("内置模型" in item["desc"] for item in rows)

    def test_help_panel_shows_choice_key(self, isolated_rc):
        mv = _open_new_form(_state())
        _enter_model_field(mv)
        _dispatch(mv, _ev("escape"))
        _dispatch(mv, _ev("char", "?"))
        _dispatch(mv, _ev("char", "G"))  # 滚到面板末（键位表末尾才有候选行）
        from src.tui.ink import h, renderToString
        m = AppModel()
        m.model_view = mv
        m.fullscreen = "model"
        out = renderToString(h(ModelView, {"model": m, "width": 100}), {"columns": 100})
        assert "内置模型" in out
