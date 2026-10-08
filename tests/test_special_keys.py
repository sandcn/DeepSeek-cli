"""src/app_loop/_special_keys — 特殊按键回调工厂单元测试。

覆盖：
  - editmsg / retry / 未知 action
  - vim：monitor 终端模式切换 + 底部栏拆装 + edit_in_vim_sync 委托
  - switch_model：模型列表来源（model_profiles.build_model_entries =
    模型档案 + RC ``models``，**不列内置 provider 模型**）、循环切换、
    provider 同步、未配置提示
  - toggle_theme：主题循环（CommandUiAdapter mock）
  - cycle_mode：cycle_mode + agent 重建 + 通知（'empty_mode' 旧名为兼容别名）

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


# ── switch_model（Ctrl+N：只在**配置的模型列表**中选择）────────

@pytest.fixture
def patch_rc(monkeypatch):
    """隔离 RC 配置（get_rc / update_config），返回 (rc, updates)。

    模型列表唯一来源 = ``model_profiles``（RC 顶层 ``models`` 已移除）。
    """
    import src.config.loader as loader_mod

    rc = {
        "model": "m1",
        "provider": "p-a",
        "model_profiles": [{"model": "m1"}, {"model": "m2"}, {"model": "m3"}],
    }
    updates: list = []

    def _get_rc():
        return rc

    def _update(key, value):
        updates.append((key, value))
        rc[key] = value

    monkeypatch.setattr(loader_mod, "get_rc", _get_rc)
    monkeypatch.setattr(loader_mod, "update_config", _update)
    return rc, updates


def test_switch_model_cycles_models(ctx, patch_rc):
    cb, state, session, chat_ui, _ = ctx
    result = cb("switch_model", "input-text")
    assert result == "input-text"
    assert state.model == "m2"
    assert session.model == "m2"
    assert chat_ui.model_names == ["m2"]
    assert chat_ui.notifications and "m2" in chat_ui.notifications[-1]


def test_switch_model_wraps_around(ctx, patch_rc):
    """列表末尾回绕到首个配置模型。"""
    cb, state, _, _, _ = ctx
    state.model = "m3"
    cb("switch_model", "t")
    assert state.model == "m1"  # 回绕


def test_switch_model_uses_profiles_first(ctx, patch_rc):
    """在档案序列中循环切换（写 active_model_profile 指针）。"""
    rc, updates = patch_rc
    rc["model_profiles"] = [{
        "model": "profile-1", "provider": "custom",
        "base_url": "http://h/v1", "api_key": "sk-k",
    }, {"model": "m1"}, {"model": "m2"}, {"model": "m3"}]
    cb, state, session, _, _ = ctx
    state.model = "m3"
    cb("switch_model", "t")
    # 列表 = profile-1, m1, m2, m3 → 从末项回绕到首个档案（索引 0）
    assert state.model == "profile-1" and session.model == "profile-1"
    assert ("ACTIVE_MODEL_PROFILE", 0) in updates
    # 不再写入任何 RC 旧键（LLM 参数来自档案）
    assert not any(k in ("MODEL", "model", "provider", "base_url", "api_key")
                   for k, _ in updates)


def test_switch_model_syncs_provider(ctx, patch_rc):
    """切到不同 provider 的档案时只切换生效指针（provider 从档案解析）。"""
    rc, updates = patch_rc
    rc["model_profiles"] = [
        {"model": "deepseek-v4-pro", "provider": "deepseek"},
        {"model": "deepseek-v4-flash", "provider": "deepseek"},
    ]
    cb, state, _, _, _ = ctx
    state.model = "deepseek-v4-pro"
    cb("switch_model", "t")
    assert state.model == "deepseek-v4-flash"
    assert ("ACTIVE_MODEL_PROFILE", 1) in updates


def test_switch_model_empty_config_notifies(ctx, patch_rc):
    """未配置任何模型档案 → 提示且不改变状态。"""
    rc, _ = patch_rc
    rc["model_profiles"] = []
    cb, state, _, chat_ui, _ = ctx
    assert cb("switch_model", "t") == "t"
    assert state.model == "m1"  # 未改变
    assert chat_ui.notifications and "未配置模型" in chat_ui.notifications[-1]


def test_switch_model_current_not_in_list_starts_at_first(ctx, patch_rc):
    cb, state, _, _, _ = ctx
    state.model = "unknown-model"
    cb("switch_model", "t")
    assert state.model == "m1"


def test_switch_model_apply_failure_notifies(ctx, patch_rc, monkeypatch):
    """apply_entry 失败时提示错误、不改变状态。"""
    import src.config.model_profiles as mp_mod

    monkeypatch.setattr(mp_mod, "apply_entry", lambda entry: (False, "写入配置失败"))
    cb, state, session, chat_ui, _ = ctx
    assert cb("switch_model", "t") == "t"
    assert state.model == "m1" and session.model == ""
    assert chat_ui.notifications and "写入配置失败" in chat_ui.notifications[-1]


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


# ── cycle_mode ───────────────────────────────────────────

def test_cycle_mode_switches(ctx, monkeypatch):
    cb, _, session, chat_ui, _ = ctx
    monkeypatch.setattr(
        "src.prompt_builder.builder.cycle_mode", lambda: "simple",
    )
    monkeypatch.setattr(
        "src.prompt_builder.builder.mode_label", lambda mode=None: "简单模式",
    )

    class _FakeAgent:
        def __init__(self):
            self.rebuilt = 0

        def rebuild_system_prompt(self):
            self.rebuilt += 1

    agent = _FakeAgent()
    session._agent = agent
    result = cb("cycle_mode", "t")
    assert result == "t"
    assert agent.rebuilt == 1
    assert chat_ui.notifications and "简单模式" in chat_ui.notifications[-1]


def test_cycle_mode_legacy_empty_action_alias(ctx, monkeypatch):
    """'empty_mode' 旧 action 名作为兼容别名走同一循环逻辑。"""
    cb, _, session, chat_ui, _ = ctx
    monkeypatch.setattr(
        "src.prompt_builder.builder.cycle_mode", lambda: "empty",
    )
    monkeypatch.setattr(
        "src.prompt_builder.builder.mode_label", lambda mode=None: "空模式",
    )
    assert cb("empty_mode", "t") == "t"
    assert chat_ui.notifications and "空模式" in chat_ui.notifications[-1]


def test_cycle_mode_agent_rebuild_exception_silent(ctx, monkeypatch):
    cb, _, session, chat_ui, _ = ctx
    monkeypatch.setattr(
        "src.prompt_builder.builder.cycle_mode", lambda: "standard",
    )
    monkeypatch.setattr(
        "src.prompt_builder.builder.mode_label", lambda mode=None: "标准模式",
    )

    class _FakeAgent:
        def rebuild_system_prompt(self):
            raise RuntimeError("rebuild failed")

    session._agent = _FakeAgent()
    assert cb("cycle_mode", "t") == "t"
    assert chat_ui.notifications and "标准模式" in chat_ui.notifications[-1]
