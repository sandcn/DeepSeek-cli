"""src/app_loop/_special_keys — 特殊按键回调工厂单元测试。

覆盖：
  - editmsg / retry / 未知 action
  - vim：monitor 终端模式切换 + 底部栏拆装 + edit_in_vim_sync 委托
  - switch_model：模型列表来源（config.MODELS / defaults.PROVIDERS）、
    循环切换、provider 同步、无模型/无当前模型兜底
  - toggle_theme：主题循环（CommandUiAdapter mock）
  - empty_mode：toggle_empty_mode + agent 重建 + 通知

注：目标函数内部使用惰性 ``from X import Y``，测试直接 patch 真实
依赖模块路径（src.config / src.core.commands._model_cmd / ...）。
"""

from __future__ import annotations

import pytest

import src.app_loop._special_keys as sk


class _FakeState:
    def __init__(self, model="m1"):
        self.model = model


class _FakeSession:
    def __init__(self):
        self.model = ""
        self._agent = None


class _FakeChatUI:
    def __init__(self):
        self.teardown_calls = 0
        self.setup_calls = 0
        self.notifications = []
        self.model_names = []
        self.input = None

    def teardown_bottom_bar(self):
        self.teardown_calls += 1

    def setup_bottom_bar(self):
        self.setup_calls += 1

    def on_notification(self, msg):
        self.notifications.append(msg)

    def set_model_name(self, name):
        self.model_names.append(name)

    @property
    def bottom_bar(self):
        return self


class _FakeMonitor:
    def __init__(self):
        self.restore_calls = 0
        self.apply_calls = 0

    def restore_terminal_settings(self):
        self.restore_calls += 1

    def apply_monitor_settings(self):
        self.apply_calls += 1


@pytest.fixture
def ctx():
    state = _FakeState()
    session = _FakeSession()
    chat_ui = _FakeChatUI()
    monitor = _FakeMonitor()
    cb = sk.make_special_key_callback(None, session, state, chat_ui, monitor)
    return cb, state, session, chat_ui, monitor


# ── 简单 action ──────────────────────────────────────────

def test_editmsg_action(ctx):
    cb, *_ = ctx
    assert cb("editmsg", "text") == "/editmsg"


def test_retry_action(ctx):
    cb, *_ = ctx
    assert cb("retry", "text") == "/retry"


def test_unknown_action_returns_none(ctx):
    cb, *_ = ctx
    assert cb("unknown_action", "text") is None


# ── vim ──────────────────────────────────────────────────

def test_vim_action_full_path(ctx, monkeypatch):
    cb, _, _, chat_ui, monitor = ctx
    edited = []

    def fake_edit(text):
        edited.append(text)
        return "edited-result"

    monkeypatch.setattr(sk, "edit_in_vim_sync", fake_edit)
    result = cb("vim", "draft")
    assert result == "edited-result"
    assert edited == ["draft"]
    assert monitor.restore_calls == 1
    assert monitor.apply_calls == 1
    assert chat_ui.teardown_calls == 1
    assert chat_ui.setup_calls == 1


def test_vim_action_without_monitor(ctx, monkeypatch):
    """monitor=None 时跳过终端模式切换。"""
    state = _FakeState()
    session = _FakeSession()
    chat_ui2 = _FakeChatUI()
    cb2 = sk.make_special_key_callback(None, session, state, chat_ui2, None)
    monkeypatch.setattr(sk, "edit_in_vim_sync", lambda text: "ok")
    assert cb2("vim", "t") == "ok"
    assert chat_ui2.teardown_calls == 1
    assert chat_ui2.setup_calls == 1


def test_vim_action_teardown_even_on_error(ctx, monkeypatch):
    """edit_in_vim_sync 抛异常时 finally 仍恢复底部栏/终端。"""
    cb, _, _, chat_ui, monitor = ctx

    def boom(text):
        raise RuntimeError("vim died")

    monkeypatch.setattr(sk, "edit_in_vim_sync", boom)
    with pytest.raises(RuntimeError):
        cb("vim", "draft")
    assert chat_ui.setup_calls == 1
    assert monitor.apply_calls == 1


# ── switch_model ─────────────────────────────────────────

@pytest.fixture
def patch_model_source(monkeypatch):
    """把模型来源固定在 config.MODELS，并禁用 provider 同步副作用。"""
    import src.config as config_mod
    import src.core.commands._model_cmd as model_cmd_mod

    monkeypatch.setattr(config_mod, "MODELS", ["m1", "m2", "m3"])
    monkeypatch.setattr(model_cmd_mod, "_infer_model_provider", lambda m: None)
    return config_mod.MODELS


def test_switch_model_cycles_models(ctx, monkeypatch, patch_model_source):
    cb, state, session, chat_ui, _ = ctx
    result = cb("switch_model", "input-text")
    assert result == "input-text"
    assert state.model == "m2"
    assert session.model == "m2"
    assert chat_ui.model_names == ["m2"]
    assert chat_ui.notifications and "m2" in chat_ui.notifications[-1]


def test_switch_model_wraps_around(ctx, monkeypatch, patch_model_source):
    """合并 PROVIDERS 为空时（仅 m1/m2/m3）循环回绕到首个模型。"""
    import src.config.defaults as defaults_mod
    monkeypatch.setattr(defaults_mod, "PROVIDERS", {})
    cb, state, session, _, _ = ctx
    state.model = "m3"
    cb("switch_model", "t")
    assert state.model == "m1"  # 回绕


def test_switch_model_merges_provider_models(ctx, monkeypatch):
    """RC models 未包含的 provider 新模型自动进入切换列表（Ctrl+N 可达）。"""
    import src.config as config_mod
    import src.core.commands._model_cmd as model_cmd_mod

    monkeypatch.setattr(config_mod, "MODELS", ["deepseek-v4-pro", "deepseek-v4-flash"])
    monkeypatch.setattr(model_cmd_mod, "_infer_model_provider", lambda m: "deepseek")
    cb, state, session, _, _ = ctx
    state.model = "deepseek-v4-flash"
    cb("switch_model", "t")
    # 下一个即 PROVIDERS 聚合追加的 deepseek-v4-flash-vision-exp
    assert state.model == "deepseek-v4-flash-vision-exp"
    assert session.model == "deepseek-v4-flash-vision-exp"


def test_switch_model_current_not_in_list(ctx, monkeypatch, patch_model_source):
    cb, state, session, _, _ = ctx
    state.model = "unknown-model"
    cb("switch_model", "t")
    assert state.model == "m1"


def test_switch_model_updates_provider(ctx, monkeypatch):
    """provider 变化时调用 update_config 同步。"""
    import src.config as config_mod
    import src.core.commands._model_cmd as model_cmd_mod
    import src.config.loader as loader_mod

    monkeypatch.setattr(config_mod, "MODELS", ["m1", "m2"])
    monkeypatch.setattr(model_cmd_mod, "_infer_model_provider", lambda m: "provider-b")
    monkeypatch.setattr(loader_mod, "get_rc", lambda: {"provider": "provider-a"})
    updates = []
    monkeypatch.setattr(loader_mod, "update_config", lambda key, val: updates.append((key, val)))

    cb, state, session, _, _ = ctx
    cb("switch_model", "t")
    assert updates == [("provider", "provider-b")]


def test_switch_model_empty_models_returns_none(ctx, monkeypatch):
    cb, state, session, _, _ = ctx
    import src.config as config_mod
    import src.config.defaults as defaults_mod

    monkeypatch.setattr(config_mod, "MODELS", [])
    monkeypatch.setattr(defaults_mod, "PROVIDERS", {})
    assert cb("switch_model", "t") is None
    assert state.model == "m1"  # 未改变


def test_switch_model_no_current_model(ctx, monkeypatch):
    cb, state, session, _, _ = ctx
    import src.config as config_mod

    monkeypatch.setattr(config_mod, "MODELS", ["a", "b"])
    state.model = ""
    assert cb("switch_model", "t") is None


# ── toggle_theme ─────────────────────────────────────────

def test_toggle_theme_cycles(ctx, monkeypatch):
    cb, _, _, chat_ui, _ = ctx

    class _FakeAdapter:
        def __init__(self):
            self.set_called = None

        def get_theme_names_with_desc(self):
            return [("dark", "深色"), ("light", "浅色")]

        def get_active_theme(self):
            return "dark"

        def set_theme(self, name):
            self.set_called = name

    adapter = _FakeAdapter()
    monkeypatch.setattr(
        "src.core.commands._ui_adapter.CommandUiAdapter", lambda: adapter,
    )
    result = cb("toggle_theme", "text")
    assert result == "text"
    assert adapter.set_called == "light"
    assert chat_ui.notifications and "light" in chat_ui.notifications[-1]


def test_toggle_theme_single_theme_no_change(ctx, monkeypatch):
    cb, _, _, chat_ui, _ = ctx

    class _FakeAdapter:
        def get_theme_names_with_desc(self):
            return [("dark", "深色")]

        def get_active_theme(self):
            return "dark"

        def set_theme(self, name):
            raise AssertionError("不应切换")

    monkeypatch.setattr(
        "src.core.commands._ui_adapter.CommandUiAdapter", lambda: _FakeAdapter(),
    )
    assert cb("toggle_theme", "text") == "text"
    assert chat_ui.notifications == []


def test_toggle_theme_exception_silent(ctx, monkeypatch):
    cb, _, _, chat_ui, _ = ctx

    def boom():
        raise RuntimeError("adapter failed")

    monkeypatch.setattr(
        "src.core.commands._ui_adapter.CommandUiAdapter", boom,
    )
    assert cb("toggle_theme", "text") == "text"  # 异常被吞，返回原文本


# ── empty_mode ───────────────────────────────────────────

def test_empty_mode_toggles(ctx, monkeypatch):
    cb, _, session, chat_ui, _ = ctx
    monkeypatch.setattr(
        "src.prompt_builder.builder.toggle_empty_mode", lambda: True,
    )

    class _FakeAgent:
        def __init__(self):
            self.rebuilt = 0

        def rebuild_system_prompt(self):
            self.rebuilt += 1

    agent = _FakeAgent()
    session._agent = agent
    result = cb("empty_mode", "t")
    assert result == "t"
    assert agent.rebuilt == 1
    assert chat_ui.notifications and "进入" in chat_ui.notifications[-1]


def test_empty_mode_agent_rebuild_exception_silent(ctx, monkeypatch):
    cb, _, session, chat_ui, _ = ctx
    monkeypatch.setattr(
        "src.prompt_builder.builder.toggle_empty_mode", lambda: False,
    )

    class _FakeAgent:
        def rebuild_system_prompt(self):
            raise RuntimeError("rebuild failed")

    session._agent = _FakeAgent()
    assert cb("empty_mode", "t") == "t"
    assert chat_ui.notifications and "退出" in chat_ui.notifications[-1]
