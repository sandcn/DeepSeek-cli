"""模型选择器（ModelView）测试 — 2026-10-09 用户需求。

覆盖：
  1. ``config.model_profiles`` 纯逻辑：字段元数据 / normalize / validate /
     load / save / build_model_entries（**模型列表唯一来源 = 模型档案**，
     RC 顶层 ``models`` 字段已移除）/ apply_entry / 提供商候选 / 默认接口
     地址 / 推断与脱敏；
  2. ``ModelViewState`` 跨线程终态协议与 reset_edit_state；
  3. 视图注册表 / 清单条目（ui_view_model、model_keymap 数据表）；
  4. ``ModelView`` 事件处理：浏览（选择应用 / a 新增 / e 编辑 / d 删除 /
     ? 帮助 / 搜索 / 复制 / Esc 关闭）；表单（字段导航 / 提供商选择 /
     字段输入 / s 保存 / Esc 取消）；空态渲染；
  5. 组件渲染（renderToString）；
  6. ``_cmd_models`` 无 ChatUI 文本回退；
  7. 补全引擎：/models 命令存在、/model 仍走参数补全、档案模型并入。
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


def _profile_entry(index=0, name="我的模型", model="qwen2.5", base="http://localhost:11434/v1",
                   key="sk-abcdef123456", provider="custom"):
    return {
        "kind": "profile", "index": index, "name": name, "model": model,
        "base_url": base, "effective_base_url": base, "api_key": key,
        "provider": provider, "current": False, "editable": True, "hint": "自定义",
    }


def _foreign_entry(kind="builtin", name="未知条目"):
    """非档案条目（防御分支：仅模型档案可编辑/删除）。"""
    return {
        "kind": kind, "index": None, "name": name, "model": name,
        "base_url": "", "effective_base_url": "", "api_key": "",
        "provider": "", "current": False, "editable": False, "hint": "",
    }


def _state(entries=None):
    return ModelViewState(visible=True, entries=entries if entries is not None else [_profile_entry()])


def _dispatch(mv, entries, event, pane_vh=10):
    return _handle_model_event(
        mv, list(entries), event, visible=True, total=len(entries),
        all_entries=list(entries), view_map=None, pane_vh=pane_vh,
    )


# ═══════════════════════════════════════════════════════════
# 1. model_profiles 纯逻辑
# ═══════════════════════════════════════════════════════════

class TestModelProfiles:

    def test_fields_and_empty(self):
        assert tuple(f["key"] for f in mp.PROFILE_FIELDS) == mp.FIELD_KEYS
        assert set(mp.FIELD_KEYS) == {"provider", "model", "api_key", "base_url", "name"}
        # 交互引导顺序：提供商 → 模型名 → API 密钥 → 接口地址 → 名称
        assert mp.FIELD_KEYS[:3] == ("provider", "model", "api_key")
        assert mp.empty_profile() == {k: "" for k in mp.FIELD_KEYS}

    def test_provider_field_is_select(self):
        assert (mp.field_by_key("provider") or {}).get("kind") == "select"
        assert (mp.field_by_key("model") or {}).get("kind") == "input"

    def test_normalize_drops_unknown_and_stringizes(self):
        out = mp.normalize_profile({"name": "a", "model": 123, "extra": "x"})
        assert out["name"] == "a" and out["model"] == "123"
        assert "extra" not in out
        assert mp.normalize_profile(None) == mp.empty_profile()

    def test_display_name(self):
        assert mp.display_name({"name": "", "model": "m"}) == "m"
        assert mp.display_name({"name": "n", "model": "m"}) == "n"
        assert mp.display_name(None) == ""

    def test_validate_model_required_and_dup(self):
        profiles = [mp.normalize_profile({"name": "a", "model": "m"})]
        assert mp.validate_profile({"name": "", "model": ""}, profiles) == "模型名不能为空"
        assert "已存在" in mp.validate_profile({"name": "a", "model": "m2"}, profiles)
        # 名称留空 → 显示名回退模型名，故与已有 "a"+model"a" 冲突
        assert "已存在" in mp.validate_profile({"name": "", "model": "a"}, profiles)
        # 编辑自身（index=0）不判重名
        assert mp.validate_profile({"name": "a", "model": "m"}, profiles, index=0) == ""
        # 名称可留空（只要不与其它显示名冲突）
        assert mp.validate_profile({"name": "", "model": "unique"}, profiles) == ""

    def test_mask_key(self):
        assert mp.mask_key("") == ""
        assert mp.mask_key("short") == "*****"
        masked = mp.mask_key("sk-abcdef123456")
        assert masked.startswith("sk-") and masked.endswith("3456") and "..." in masked

    def test_infer_and_resolve_provider(self):
        assert mp.infer_provider("deepseek-v4-pro") == "deepseek"
        assert mp.infer_provider("totally-unknown") == ""
        assert mp.infer_provider("ollama/qwen") == "custom"
        prof = mp.normalize_profile({"name": "p", "model": "x", "provider": "glm"})
        assert mp.resolve_provider(prof, "deepseek") == "glm"
        prof2 = mp.normalize_profile({"name": "p", "model": "deepseek-v4-pro"})
        assert mp.resolve_provider(prof2, "custom") == "deepseek"
        prof3 = mp.normalize_profile({"name": "p", "model": "unknown-model"})
        assert mp.resolve_provider(prof3, "glm") == "glm"

    def test_default_and_effective_base_url(self):
        assert mp.default_base_url("deepseek").startswith("https://api.deepseek.com")
        assert mp.default_base_url("nonexistent-provider") == ""
        prof = mp.normalize_profile({"name": "p", "model": "m", "base_url": "http://x"})
        assert mp.effective_base_url(prof, "custom") == "http://x"
        prof2 = mp.normalize_profile({"name": "p", "model": "m", "provider": "deepseek"})
        assert mp.effective_base_url(prof2).startswith("https://api.deepseek.com")

    def test_provider_choices(self):
        choices = mp.provider_choices()
        names = [c[0] for c in choices]
        assert "deepseek" in names and "custom" in names and "anthropic" in names
        assert all(c[1] for c in choices)  # 每项有说明

    def test_save_load_roundtrip(self, isolated_rc):
        profiles = [mp.normalize_profile(
            {"provider": "deepseek", "model": "m1", "api_key": "k", "base_url": "u"},
        )]
        mp.save_profiles(profiles)
        loaded = mp.load_profiles()
        assert len(loaded) == 1 and loaded[0]["model"] == "m1" and loaded[0]["base_url"] == "u"

    def test_build_model_entries_profiles_only(self, isolated_rc):
        from src.config.loader import update_config
        mp.save_profiles([mp.normalize_profile(
            {"provider": "custom", "model": "my-model", "base_url": "http://h/v1", "api_key": "k1"},
        )])
        update_config("MODEL", "my-model")
        update_config("base_url", "http://h/v1")
        entries = mp.build_model_entries()
        # 仅模型档案（RC models 字段已移除，内置模型不进列表）
        assert len(entries) == 1
        e = entries[0]
        assert e["kind"] == "profile" and e["current"] is True
        assert e["provider"] == "custom" and e["effective_base_url"] == "http://h/v1"

    def test_build_model_entries_empty_without_profiles(self):
        assert mp.build_model_entries(rc={"model_profiles": []}) == []

    def test_build_model_entries_ignores_legacy_models_key(self):
        """RC 历史遗留 ``models`` 键不再产生条目（唯一来源 = 模型档案）。"""
        rc = {"model_profiles": [], "models": ["a", "b"]}
        assert mp.build_model_entries(rc=rc) == []
        assert mp.configured_models(rc=rc) == []

    def test_configured_models_matches_entries(self):
        """Ctrl+N / ``/model`` 候选 = build_model_entries 的模型序列。"""
        rc = {"model_profiles": [{"model": "p1"}, {"model": "p2"}], "models": ["legacy"]}
        assert mp.configured_models(rc=rc) == [e["model"] for e in mp.build_model_entries(rc=rc)]

    def test_configured_models_empty_rc(self):
        assert mp.configured_models(rc={}) == []

    def test_configured_models_dedup_and_order(self):
        # 档案模型名，去重保序
        rc = {"model_profiles": [{"model": "p1"}, {"model": "p2"}, {"model": "p1"}]}
        assert mp.configured_models(rc=rc) == ["p1", "p2"]

    def test_configured_models_from_saved_profile(self, isolated_rc):
        mp.save_profiles([{"model": "saved-1"}])
        rc = {"model_profiles": mp.load_profiles()}
        assert mp.configured_models(rc=rc) == ["saved-1"]

    def test_apply_entry_sets_active_profile(self, isolated_rc):
        """apply_entry 只切换「当前生效档案」指针（LLM 参数来自档案）。"""
        from src.config.loader import get_rc
        mp.save_profiles([
            mp.normalize_profile({"model": "m", "provider": "glm",
                                  "base_url": "http://h/v1", "api_key": "sk-newkey12345"}),
        ])
        ok, msg = mp.apply_entry(_profile_entry(
            index=0, model="m", base="http://h/v1", key="sk-newkey12345", provider="glm",
        ))
        assert ok and "m" in msg
        rc = get_rc()
        assert rc["active_model_profile"] == 0
        # 不再写入任何 RC 旧键
        for legacy in ("model", "provider", "base_url", "api_key"):
            assert legacy not in rc
        # 生效值经档案解析
        assert mp.current_model(rc) == "m"
        assert mp.current_provider(rc) == "glm"
        assert mp.current_base_url(rc) == "http://h/v1"
        assert mp.current_api_key(rc) == "sk-newkey12345"

    def test_apply_entry_rejects_unknown_profile(self, isolated_rc):
        mp.save_profiles([{"model": "known"}])
        ok, msg = mp.apply_entry({"kind": "profile", "index": 5, "model": "nope"})
        assert not ok and "不存在" in msg

    def test_apply_entry_rejects_empty_model(self):
        ok, msg = mp.apply_entry({"kind": "profile", "model": ""})
        assert not ok and msg

    def test_schema_removes_legacy_models_key(self, isolated_rc):
        """加载 RC 时清理历史遗留的顶层 ``models`` 字段（内存 + 磁盘）。"""
        import json
        from src.config.loader import RC_FILE, get_rc
        RC_FILE.write_text(json.dumps({
            "model": "m1", "models": ["m1", "legacy-2"], "model_profiles": [],
        }), encoding="utf-8")
        rc = get_rc()
        assert "models" not in rc
        # 一次性落盘清理：遗留键从磁盘文件一并删除
        assert "models" not in json.loads(RC_FILE.read_text(encoding="utf-8"))


# ═══════════════════════════════════════════════════════════
# 2. ModelViewState
# ═══════════════════════════════════════════════════════════

class TestModelViewState:

    def test_try_set_final_first_write_wins(self):
        st = ModelViewState()
        assert st.try_set_final("cancel") is True
        assert st.done and st.action == "cancel"
        assert st.try_set_final("timeout") is False
        assert st.action == "cancel"

    def test_reset_edit_state(self):
        st = ModelViewState()
        st.editing = True
        st.edit_mode = "select"
        st.form_values = {"model": "x"}
        st.form_is_new = True
        st.form_select_options = ["a"]
        st.form_select_index = 2
        st.edit_error = "err"
        st.message = "msg"
        st.reset_edit_state()
        assert not st.editing and st.edit_mode == "form"
        assert st.form_values == {} and st.form_index is None
        assert st.form_select_options == [] and st.form_select_index == 0
        assert st.edit_error == "" and st.message == ""

    def test_appmodel_has_model_view_and_reset_keeps_seq(self):
        m = AppModel()
        assert isinstance(m.model_view, ModelViewState)
        m.model_view.seq = 7
        m.model_view.visible = True
        m.reset_display()
        assert m.model_view.visible is False
        assert m.model_view.seq == 7


# ═══════════════════════════════════════════════════════════
# 3. 视图注册 / 数据表 / 清单
# ═══════════════════════════════════════════════════════════

class TestRegistration:

    def test_view_registry_has_model(self):
        import src.tui.app.view_registry as vreg
        assert "model" in vreg.builtin_view_ids()
        assert "model" in vreg.fullscreen_views()
        from src.tui.app.app import FULLSCREEN_VIEWS
        assert "model" in FULLSCREEN_VIEWS

    def test_model_keymap_table(self):
        from src.presentation_data import model_keymap, MODEL_KEYMAP_DATA
        assert model_keymap() == MODEL_KEYMAP_DATA and len(model_keymap()) > 0

    def test_manifest_ui_view_and_keymap(self):
        from src.plugins.manifest import UI_VIEW_ENTRIES, PRESENTATION_DATA_ENTRIES
        view_ids = {(e.get("config") or {}).get("id") for e in UI_VIEW_ENTRIES}
        assert "model" in view_ids
        table_ids = {(e.get("config") or {}).get("id") for e in PRESENTATION_DATA_ENTRIES}
        assert "model_keymap" in table_ids


# ═══════════════════════════════════════════════════════════
# 4. 事件处理
# ═══════════════════════════════════════════════════════════

class TestBrowseEvents:

    def test_enter_applies_selected(self):
        mv = _state()
        entries = list(mv.entries)
        assert _dispatch(mv, entries, _ev("enter")) is True
        assert mv.applied_seq == 1 and mv.applied is entries[0]

    def test_escape_closes(self):
        mv = _state()
        assert _dispatch(mv, mv.entries, _ev("escape")) is True
        assert mv.done and mv.action == "cancel"

    def test_a_opens_new_form_prefilled(self, isolated_rc):
        mv = _state()
        assert _dispatch(mv, mv.entries, _ev("char", "a")) is True
        assert mv.editing and mv.edit_mode == "form" and mv.form_is_new
        assert mv.form_index is None
        provider = mv.form_values["provider"]
        assert provider  # 预填当前提供商
        assert mv.form_values["base_url"] == mp.default_base_url(provider)

    def test_e_edits_profile(self, isolated_rc):
        mv = _state()
        mv.selected = 0
        assert _dispatch(mv, mv.entries, _ev("char", "e")) is True
        assert mv.editing and mv.form_is_new is False and mv.form_index == 0
        assert mv.form_values["name"] == "我的模型"

    def test_d_confirm_then_delete(self, isolated_rc):
        mp.save_profiles([mp.normalize_profile({"name": "待删", "model": "m"})])
        mv = _state()
        mv.selected = 0
        assert _dispatch(mv, mv.entries, _ev("char", "d")) is True
        assert mv.editing and mv.edit_mode == "confirm"
        assert _dispatch(mv, mv.entries, _ev("enter")) is True
        assert not mv.editing and "已删除" in mv.message
        assert mp.load_profiles() == []

    def test_d_confirm_escape_cancels(self):
        mv = _state()
        mv.selected = 0
        _dispatch(mv, mv.entries, _ev("char", "d"))
        assert mv.edit_mode == "confirm"
        _dispatch(mv, mv.entries, _ev("escape"))
        assert not mv.editing and mv.edit_mode == "form"

    def test_e_on_non_profile_entry_shows_hint(self):
        mv = _state(entries=[_foreign_entry()])
        mv.selected = 0
        assert _dispatch(mv, mv.entries, _ev("char", "e")) is True
        assert not mv.editing and "仅模型档案可编辑" in mv.message

    def test_d_on_non_profile_entry_shows_hint(self):
        mv = _state(entries=[_foreign_entry()])
        mv.selected = 0
        assert _dispatch(mv, mv.entries, _ev("char", "d")) is True
        assert not mv.editing and "仅模型档案可删除" in mv.message

    def test_delete_selected_rejects_non_profile_entry(self):
        from src.tui.app.model_view import _delete_selected
        mv = _state()
        _delete_selected(mv, _foreign_entry())
        assert not mv.editing and "仅模型档案可删除" in mv.message

    def test_question_opens_help_and_closes(self):
        mv = _state()
        assert _dispatch(mv, mv.entries, _ev("char", "?")) is True
        assert mv.help_open is True
        assert _dispatch(mv, mv.entries, _ev("char", "q")) is True
        assert mv.help_open is False

    def test_copy_sets_message(self, monkeypatch):
        mv = _state()
        monkeypatch.setattr("src.tui._screen.set_clipboard", lambda text: True)
        assert _dispatch(mv, mv.entries, _ev("char", "y")) is True
        assert "已复制" in mv.message


class TestSearchEvents:

    def test_search_flow_and_jump(self):
        mv = _state()
        assert _dispatch(mv, mv.entries, _ev("char", "/")) is True
        assert mv.search_mode is True
        for ch in "qwen":
            _dispatch(mv, mv.entries, _ev("char", ch))
        _dispatch(mv, mv.entries, _ev("backspace"))
        _dispatch(mv, mv.entries, _ev("enter"))
        assert mv.search_mode is False
        assert mv.search_pattern == "qwe"
        assert mv.search_matches == [0]

    def test_search_matches_and_filter(self):
        mv = _state([_profile_entry(0, model="alpha-model"), _profile_entry(1, model="beta-model")])
        _dispatch(mv, mv.entries, _ev("char", "/"))
        for ch in "beta":
            _dispatch(mv, mv.entries, _ev("char", ch))
        _dispatch(mv, mv.entries, _ev("enter"))
        assert mv.search_matches == [1]
        assert _dispatch(mv, mv.entries, _ev("char", "f")) is True
        assert mv.search_filter is True

    def test_search_escape_cancels(self):
        mv = _state()
        _dispatch(mv, mv.entries, _ev("char", "/"))
        _dispatch(mv, mv.entries, _ev("escape"))
        assert mv.search_mode is False and mv.search_query == ""


class TestFormEvents:

    def test_model_field_edit_and_commit(self):
        mv = _state()
        _dispatch(mv, mv.entries, _ev("char", "a"))
        mv.form_selected = 1  # 模型名
        assert _dispatch(mv, mv.entries, _ev("enter")) is True
        assert mv.edit_mode == "field"
        for ch in "abc":
            _dispatch(mv, mv.entries, _ev("char", ch))
        assert mv.form_edit_value == "abc"
        _dispatch(mv, mv.entries, _ev("backspace"))
        assert mv.form_edit_value == "ab"
        assert _dispatch(mv, mv.entries, _ev("enter")) is True
        assert mv.edit_mode == "form" and mv.form_values["model"] == "ab"

    def test_field_ctrl_u_clears(self):
        mv = _state()
        _dispatch(mv, mv.entries, _ev("char", "a"))
        mv.form_selected = 1
        _dispatch(mv, mv.entries, _ev("enter"))
        _dispatch(mv, mv.entries, _ev("char", "x"))
        assert _dispatch(mv, mv.entries, _ev("ctrl_key", "\x15")) is True
        assert mv.form_edit_value == ""

    def test_field_escape_cancels(self):
        mv = _state()
        _dispatch(mv, mv.entries, _ev("char", "a"))
        mv.form_selected = 1
        _dispatch(mv, mv.entries, _ev("enter"))
        _dispatch(mv, mv.entries, _ev("char", "x"))
        _dispatch(mv, mv.entries, _ev("escape"))
        assert mv.edit_mode == "form" and mv.form_edit_value == ""

    def test_provider_select_flow(self):
        mv = _state()
        _dispatch(mv, mv.entries, _ev("char", "a"))
        mv.form_selected = 0  # 提供商
        assert _dispatch(mv, mv.entries, _ev("enter")) is True
        assert mv.edit_mode == "select"
        opts = list(mv.form_select_options)
        assert "deepseek" in opts and "anthropic" in opts
        # 选择 anthropic → 自动填其接口地址
        mv.form_select_index = opts.index("anthropic")
        assert _dispatch(mv, mv.entries, _ev("enter")) is True
        assert mv.edit_mode == "form"
        assert mv.form_values["provider"] == "anthropic"
        assert mv.form_values["base_url"] == mp.default_base_url("anthropic")

    def test_provider_select_escape(self):
        mv = _state()
        _dispatch(mv, mv.entries, _ev("char", "a"))
        mv.form_selected = 0
        _dispatch(mv, mv.entries, _ev("enter"))
        assert mv.edit_mode == "select"
        _dispatch(mv, mv.entries, _ev("escape"))
        assert mv.edit_mode == "form"

    def test_form_save_new_profile_name_defaults_to_model(self, isolated_rc):
        mv = _state()
        _dispatch(mv, mv.entries, _ev("char", "a"))
        mv.form_values = mp.normalize_profile(
            {"provider": "deepseek", "model": "m-new", "api_key": "k"},
        )
        assert _dispatch(mv, mv.entries, _ev("char", "s")) is True
        assert not mv.editing and "已新增" in mv.message
        saved = mp.load_profiles()
        assert len(saved) == 1
        assert saved[0]["model"] == "m-new"
        assert saved[0]["name"] == "m-new"  # 名称留空 → 用模型名

    def test_form_save_validation_error(self, isolated_rc):
        mv = _state()
        _dispatch(mv, mv.entries, _ev("char", "a"))
        mv.form_values = mp.normalize_profile({"provider": "deepseek", "model": ""})
        _dispatch(mv, mv.entries, _ev("char", "s"))
        assert mv.editing and mv.edit_error == "模型名不能为空"

    def test_form_escape_cancels(self):
        mv = _state()
        _dispatch(mv, mv.entries, _ev("char", "a"))
        _dispatch(mv, mv.entries, _ev("escape"))
        assert not mv.editing

    def test_form_edit_existing(self, isolated_rc):
        mp.save_profiles([mp.normalize_profile({"model": "m1", "provider": "deepseek"})])
        mv = _state()
        mv.selected = 0
        _dispatch(mv, mv.entries, _ev("char", "e"))
        mv.form_values = mp.normalize_profile({"provider": "glm", "model": "m2"})
        _dispatch(mv, mv.entries, _ev("char", "s"))
        saved = mp.load_profiles()
        assert saved[0]["model"] == "m2" and saved[0]["provider"] == "glm"


class TestPaste:

    def test_field_paste(self):
        mv = _state()
        _dispatch(mv, mv.entries, _ev("char", "a"))
        mv.form_selected = 1
        _dispatch(mv, mv.entries, _ev("enter"))
        assert _handle_model_paste(mv, True, "abc\ndef") is True
        assert mv.form_edit_value == "abcdef"

    def test_search_paste(self):
        mv = _state()
        _dispatch(mv, mv.entries, _ev("char", "/"))
        assert _handle_model_paste(mv, True, "xy\nz") is True
        assert mv.search_query == "xyz"

    def test_paste_ignored_when_browsing(self):
        mv = _state()
        assert _handle_model_paste(mv, True, "abc") is False


# ═══════════════════════════════════════════════════════════
# 5. 组件渲染
# ═══════════════════════════════════════════════════════════

class TestRender:

    def _render(self, mv, width=80):
        from src.tui.ink import h, renderToString
        m = AppModel()
        m.model_view = mv
        m.fullscreen = "model"
        return renderToString(h(ModelView, {"model": m, "width": width}), {"columns": width})

    def test_renders_title_and_rows(self):
        mv = _state()
        out = self._render(mv)
        assert "模型选择器" in out and "我的模型" in out

    def test_empty_state_hint(self):
        mv = _state(entries=[])
        out = self._render(mv)
        assert "模型选择器" in out and "暂无模型档案" in out

    def test_hidden_renders_empty(self):
        from src.tui.ink import h, renderToString
        m = AppModel()  # model_view.visible 默认 False
        out = renderToString(h(ModelView, {"model": m, "width": 80}), {"columns": 80})
        assert out.strip() == ""

    def test_renders_form_fields(self):
        mv = _state()
        _dispatch(mv, mv.entries, _ev("char", "a"))
        out = self._render(mv)
        assert "新增模型档案" in out
        assert "提供商" in out and "模型名" in out and "API 密钥" in out

    def test_renders_provider_select(self):
        mv = _state()
        _dispatch(mv, mv.entries, _ev("char", "a"))
        mv.form_selected = 0
        _dispatch(mv, mv.entries, _ev("enter"))
        out = self._render(mv)
        assert "deepseek" in out and "anthropic" in out
        assert "选择提供商" in out

    def test_sensitive_field_masked_in_form(self):
        mv = _state()
        mv.selected = 0
        _dispatch(mv, mv.entries, _ev("char", "e"))
        out = self._render(mv)
        assert "sk-abcdef123456" not in out


# ═══════════════════════════════════════════════════════════
# 6. 命令层
# ═══════════════════════════════════════════════════════════

class TestCommandLayer:

    def test_open_model_view_returns_false_without_chat_ui(self, monkeypatch):
        from src.core.commands import _model_cmd
        monkeypatch.setattr(
            "src.core.adapters.ui_runtime.get_active_chat_ui", lambda: None,
        )
        ctx = SimpleNamespace(session=None, state={})
        assert _model_cmd._open_model_view(ctx) is False

    def test_cmd_models_text_fallback(self, monkeypatch):
        from src.core.commands import _model_cmd
        monkeypatch.setattr(
            "src.core.adapters.ui_runtime.get_active_chat_ui", lambda: None,
        )
        captured: list[str] = []
        monkeypatch.setattr(
            _model_cmd._out, "write",
            lambda text, level="info", source="cmd": captured.append(text),
        )
        ctx = SimpleNamespace(session=None, state={})
        assert _model_cmd._cmd_models(ctx) is True
        assert "模型选择器" in "\n".join(captured)

    def test_models_command_declared_and_registered(self):
        import src.core.commands.plugins.models_plugin  # noqa: F401
        from src.core.commands.base import get_plugin_registry
        assert get_plugin_registry().exists("models")

    def test_apply_model_entry_syncs_session(self, isolated_rc):
        from src.core.commands import _model_cmd
        mp.save_profiles([{"model": "qwen", "provider": "custom"}])
        captured: list[str] = []
        monkeypatch = pytest.MonkeyPatch()
        try:
            monkeypatch.setattr(
                _model_cmd._out, "write",
                lambda text, level="info", source="cmd": captured.append(text),
            )
            session = SimpleNamespace(model="old")
            state: dict = {"model": "old"}
            ctx = SimpleNamespace(state=state)
            _model_cmd._apply_model_entry(
                ctx, session, None,
                _profile_entry(index=0, name="qwen", model="qwen", key=""),
            )
            assert session.model == "qwen" and state["model"] == "qwen"
        finally:
            monkeypatch.undo()


# ═══════════════════════════════════════════════════════════
# 7. 补全引擎
# ═══════════════════════════════════════════════════════════

class TestCompletion:

    def test_model_param_completion_still_works(self, monkeypatch):
        from src.tui import _completion_engine as ce
        eng = ce.CompletionEngine()
        eng.register_source("models", lambda: ["m-a", "m-b"])
        items = eng.complete("/model")
        assert all(i.item_type == "param" for i in items)
        assert any(i.text.startswith("/model ") for i in items)

    def test_fetch_models_config_only(self, monkeypatch):
        """补全只列配置的模型（不列内置 provider 模型）。"""
        from src.tui._completion_engine import CompletionEngine
        from src.config import model_profiles as mp_mod
        monkeypatch.setattr(mp_mod, "configured_models", lambda rc=None: [])
        assert CompletionEngine._fetch_models() == []
        monkeypatch.setattr(
            mp_mod, "configured_models", lambda rc=None: ["my-custom-model"],
        )
        # 完全相等 = 未混入任何内置 provider 模型
        assert CompletionEngine._fetch_models() == ["my-custom-model"]


# ═══════════════════════════════════════════════════════════
# 8. 美化与体验增强（2026-10-09 用户需求「美化，优化用户体验和操作 /models」）
# ═══════════════════════════════════════════════════════════

class TestUrlHost:
    """接口地址 → 主机简写（列表行展示用）。"""

    def test_extract_host_with_scheme(self):
        from src.tui.app.model_view import _url_host
        assert _url_host("https://api.deepseek.com/v1") == "api.deepseek.com"
        assert _url_host("http://localhost:11434/v1") == "localhost:11434"

    def test_extract_host_without_scheme(self):
        from src.tui.app.model_view import _url_host
        assert _url_host("api.example.com") == "api.example.com"
        assert _url_host("api.example.com/v1") == "api.example.com"

    def test_empty_url(self):
        from src.tui.app.model_view import _url_host
        assert _url_host("") == ""
        assert _url_host(None) == ""


class TestDetailLine:
    """选中模型详情行（完整信息，随选中更新）。"""

    def test_detail_runs_contain_all_fields(self):
        from src.tui.app.model_view import _detail_runs
        runs = _detail_runs(_profile_entry(), 160)
        text = "".join(r.text for r in runs)
        assert "qwen2.5" in text           # 模型名
        assert "custom" in text            # 提供商
        assert "http://localhost:11434/v1" in text  # 完整接口地址
        assert "sk-...3456" in text        # 脱敏密钥

    def test_detail_runs_empty_entry(self):
        from src.tui.app.model_view import _detail_runs
        assert _detail_runs(None, 80) == []
        assert _detail_runs("not-a-dict", 80) == []

    def test_detail_runs_truncated_to_width(self):
        from src.tui.app.model_view import _detail_runs, _disp_width
        runs = _detail_runs(_profile_entry(), 20)
        assert 0 < _disp_width("".join(r.text for r in runs)) <= 20


class TestProfileEntryValues:

    def test_base_url_falls_back_to_effective(self):
        from src.tui.app.model_view import _profile_entry_values
        entry = {"model": "m", "provider": "custom"}  # 无 base_url
        entry["effective_base_url"] = "http://x/v1"
        values = _profile_entry_values(entry)
        assert values["base_url"] == "http://x/v1"
        assert values["model"] == "m"

    def test_missing_keys_default_empty(self):
        from src.tui.app.model_view import _profile_entry_values
        values = _profile_entry_values({"model": "m"})
        assert values["api_key"] == "" and values["name"] == ""


class TestDuplicateAndRefresh:

    def test_c_duplicates_profile_into_form(self, isolated_rc):
        mv = _state([_profile_entry(
            index=0, name="源档案", model="m-src", key="k", provider="deepseek",
        )])
        mv.selected = 0
        assert _dispatch(mv, mv.entries, _ev("char", "c")) is True
        assert mv.editing and mv.form_is_new
        assert mv.form_values["model"] == "m-src"
        assert mv.form_values["name"] == "源档案 副本"
        assert "已复制" in mv.message

    def test_c_on_non_profile_shows_hint(self):
        mv = _state(entries=[_foreign_entry()])
        mv.selected = 0
        assert _dispatch(mv, mv.entries, _ev("char", "c")) is True
        assert not mv.editing and "仅模型档案可复制" in mv.message

    def test_r_refreshes_entries(self, isolated_rc):
        mp.save_profiles([mp.normalize_profile({"model": "m1"})])
        mv = _state()
        mp.save_profiles([
            mp.normalize_profile({"model": "m1"}),
            mp.normalize_profile({"model": "m2"}),
        ])
        assert _dispatch(mv, mv.entries, _ev("char", "r")) is True
        assert len(mv.entries) == 2
        assert "已刷新" in mv.message


class TestFieldCursorEditing:
    """字段输入光标编辑（←/→/Home/End/Delete/Ctrl+A/E · 中间插入）。"""

    def _edit_model_field(self, mv):
        mv.selected = 0
        _dispatch(mv, mv.entries, _ev("char", "e"))
        mv.form_selected = 1  # 模型名
        _dispatch(mv, mv.entries, _ev("enter"))
        return mv

    def test_cursor_starts_at_end(self):
        mv = self._edit_model_field(_state())
        assert mv.edit_mode == "field"
        assert mv.form_edit_value == "qwen2.5"
        assert mv.form_edit_cursor == len("qwen2.5")

    def test_insert_at_cursor(self):
        mv = self._edit_model_field(_state())
        _dispatch(mv, mv.entries, _ev("arrow_left"))
        _dispatch(mv, mv.entries, _ev("arrow_left"))
        assert mv.form_edit_cursor == len("qwen2.5") - 2
        _dispatch(mv, mv.entries, _ev("char", "X"))
        assert mv.form_edit_value == "qwen2X.5"
        assert mv.form_edit_cursor == len("qwen2.5") - 2 + 1

    def test_home_end_and_ctrl_variants(self):
        mv = self._edit_model_field(_state())
        _dispatch(mv, mv.entries, _ev("home"))
        assert mv.form_edit_cursor == 0
        _dispatch(mv, mv.entries, _ev("char", "Z"))
        assert mv.form_edit_value.startswith("Z")
        _dispatch(mv, mv.entries, _ev("end"))
        assert mv.form_edit_cursor == len(mv.form_edit_value)
        _dispatch(mv, mv.entries, _ev("ctrl_key", "\x01"))  # Ctrl+A
        assert mv.form_edit_cursor == 0
        _dispatch(mv, mv.entries, _ev("ctrl_key", "\x05"))  # Ctrl+E
        assert mv.form_edit_cursor == len(mv.form_edit_value)

    def test_delete_at_cursor(self):
        mv = self._edit_model_field(_state())
        _dispatch(mv, mv.entries, _ev("home"))
        _dispatch(mv, mv.entries, _ev("delete"))
        assert mv.form_edit_value == "wen2.5"

    def test_backspace_at_cursor_middle(self):
        mv = self._edit_model_field(_state())
        _dispatch(mv, mv.entries, _ev("home"))
        _dispatch(mv, mv.entries, _ev("arrow_right"))
        _dispatch(mv, mv.entries, _ev("backspace"))
        assert mv.form_edit_value == "wen2.5"

    def test_ctrl_u_clears_and_resets_cursor(self):
        mv = self._edit_model_field(_state())
        _dispatch(mv, mv.entries, _ev("ctrl_key", "\x15"))  # Ctrl+U
        assert mv.form_edit_value == "" and mv.form_edit_cursor == 0

    def test_commit_resets_cursor(self):
        mv = self._edit_model_field(_state())
        _dispatch(mv, mv.entries, _ev("enter"))
        assert mv.edit_mode == "form"
        assert mv.form_edit_cursor == 0

    def test_paste_inserts_at_cursor(self):
        mv = self._edit_model_field(_state())
        _dispatch(mv, mv.entries, _ev("home"))
        assert _handle_model_paste(mv, True, "AB") is True
        assert mv.form_edit_value.startswith("AB")
        assert mv.form_edit_cursor == 2


class TestRenderEnhancements(TestRender):
    """渲染层美化（列表双列 / 详情行 / 空态 / 帮助页脚优先级）。"""

    def test_list_row_shows_model_and_host(self):
        mv = _state([_profile_entry(
            name="显示名", model="my-model", base="http://myhost.example/v1",
        )])
        out = self._render(mv)
        assert "my-model" in out          # 模型名不再被地址覆盖
        assert "myhost.example" in out    # 主机简写
        assert "http://myhost.example/v1" in out  # 详情行完整地址

    def test_detail_line_tracks_selection(self):
        mv = _state([
            _profile_entry(0, name="甲", model="model-a", base="http://a.example/v1"),
            _profile_entry(1, name="乙", model="model-b", base="http://b.example/v1"),
        ])
        mv.selected = 1
        out = self._render(mv)
        assert "http://b.example/v1" in out
        assert "\u21b3" in out  # ↳ 详情行标记

    def test_empty_state_two_line_card(self):
        mv = _state(entries=[])
        out = self._render(mv)
        assert "暂无模型档案" in out
        assert "按 a 新增" in out

    def test_help_footer_priority_over_form(self):
        mv = _state()
        _dispatch(mv, mv.entries, _ev("char", "a"))  # 进入表单
        mv.help_open = True
        out = self._render(mv)
        assert "关闭帮助" in out
        assert "s 保存  \u00b7  Esc 取消" not in out  # 表单页脚被帮助页脚抑制

    def test_no_detail_line_in_form_mode(self):
        mv = _state()
        _dispatch(mv, mv.entries, _ev("char", "a"))
        out = self._render(mv)
        assert "\u21b3" not in out


class TestKeymapData:

    def test_new_keys_documented(self):
        from src.presentation_data import MODEL_KEYMAP_DATA
        keys = {item["keys"] for item in MODEL_KEYMAP_DATA}
        assert "c" in keys and "r" in keys
        groups = {item["group"] for item in MODEL_KEYMAP_DATA}
        assert "输入" in groups  # 字段输入（光标编辑）分组


class TestStateCursorField:

    def test_default_and_reset(self):
        st = ModelViewState()
        assert st.form_edit_cursor == 0
        st.form_edit_cursor = 5
        st.reset_edit_state()
        assert st.form_edit_cursor == 0


class TestSelectionContinuity:
    """保存 / 删除后选中定位（浏览连续性）。"""

    def test_save_locates_saved_entry(self, isolated_rc):
        mp.save_profiles([mp.normalize_profile({"model": "m1"})])
        mv = ModelViewState(visible=True, entries=mp.build_model_entries())
        _dispatch(mv, mv.entries, _ev("char", "a"))
        mv.form_values = mp.normalize_profile({"provider": "deepseek", "model": "m2"})
        _dispatch(mv, mv.entries, _ev("char", "s"))
        assert len(mv.entries) == 2
        assert mv.selected == 1  # 定位到刚保存的新条目

    def test_delete_keeps_selection_nearby(self, isolated_rc):
        mp.save_profiles([{"model": "m1"}, {"model": "m2"}, {"model": "m3"}])
        mv = ModelViewState(visible=True, entries=mp.build_model_entries())
        mv.selected = 0
        _dispatch(mv, mv.entries, _ev("char", "d"))
        _dispatch(mv, mv.entries, _ev("enter"))
        assert len(mp.load_profiles()) == 2
        assert mv.selected == 0  # 原位置由后续条目顶上


class TestFormHelp:

    def test_question_in_form_opens_help(self):
        mv = _state()
        _dispatch(mv, mv.entries, _ev("char", "a"))
        assert _dispatch(mv, mv.entries, _ev("char", "?")) is True
        assert mv.help_open is True and mv.editing is True
        _dispatch(mv, mv.entries, _ev("char", "q"))
        assert mv.help_open is False and mv.editing is True  # 关闭后回到表单
